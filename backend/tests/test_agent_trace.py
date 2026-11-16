import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.services.agent_intent import (
    IntentAttemptTiming,
    IntentResolution,
    IntentResolverOutcome,
)
from app.services.agent_runtime import (
    _audit_result_summary,
    _normalize_unpersisted_proposal_result,
)
from app.services.agent_trace import (
    add_stage_timing,
    build_initial_execution_trace,
    complete_execution_trace,
    select_execution_mode,
)
from evals.multistep_schema import load_multistep_dataset
from evals.multistep_scorer import score_runtime_execution_trace


def test_execution_mode_gate_covers_direct_planned_clarify_and_safe_stop():
    direct = IntentResolution(
        primary_intent="next_workout_query",
        resolved_query="查询下一练",
        subtasks=["查询下一练"],
        confidence=0.9,
    )
    planned = direct.model_copy(update={
        "subtasks": ["检查活动训练", "查询下一练"],
    })
    clarify = direct.model_copy(update={
        "missing_slots": ["时间范围"],
        "clarification_required": True,
    })
    safe_stop = direct.model_copy(update={"risk_level": "high"})

    assert select_execution_mode(direct, ["workout.get_next"])[0] == "direct"
    assert select_execution_mode(
        planned,
        ["workout.get_active_session", "workout.get_next"],
    )[0] == "planned"
    assert select_execution_mode(
        direct,
        ["workout.get_active_session", "workout.get_next"],
    )[0] == "direct"
    assert select_execution_mode(clarify, [])[0] == "clarify"
    assert select_execution_mode(safe_stop, [])[0] == "safe_stop"


def test_direct_nutrition_answer_cannot_invite_confirmation_without_proposal():
    resolution = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="query",
        requested_effect="read",
        resolved_query="给我一个减脂晚餐建议",
        subtasks=["给出晚餐建议"],
        confidence=0.95,
    )
    trace = build_initial_execution_trace(resolution, []).model_copy(update={
        "terminal_action": "answer",
    })

    reply, normalized = _normalize_unpersisted_proposal_result(
        reply="需要我确认提交这份提案吗？",
        execution_trace=trace,
        proposal_reference=None,
        intent_domain="nutrition",
        request_kind="query",
    )

    assert reply == (
        "这次还没有生成可确认的饮食记录提案，数据没有改动。"
        "要记录一餐，请补充餐次、食品和克数。"
    )
    assert normalized.terminal_action == "answer"
    assert "proposal_creation_rejected_safe_answer" in normalized.mode_reasons


def test_direct_answer_cannot_claim_a_pending_proposal_without_reference():
    resolution = IntentResolution(
        primary_intent="nutrition_today_query",
        intent_domain="nutrition",
        request_kind="assessment",
        requested_effect="read",
        resolved_query="评估晚餐建议",
        subtasks=["给出晚餐建议"],
        confidence=0.95,
    )
    trace = build_initial_execution_trace(resolution, []).model_copy(update={
        "terminal_action": "answer",
    })

    reply, normalized = _normalize_unpersisted_proposal_result(
        reply="以上是待确认饮食提案，尚未写入。",
        execution_trace=trace,
        proposal_reference=None,
        intent_domain="nutrition",
        request_kind="assessment",
    )

    assert "还没有生成可确认的饮食记录提案" in reply
    assert "数据没有改动" in reply
    assert normalized.terminal_action == "answer"


def test_proposal_terminal_action_is_downgraded_even_when_feature_is_off():
    resolution = IntentResolution(
        primary_intent="plan_query",
        intent_domain="workout_plan",
        request_kind="assessment",
        requested_effect="read",
        resolved_query="评估当前计划",
        subtasks=["评估当前计划"],
        confidence=0.95,
    )
    trace = build_initial_execution_trace(resolution, []).model_copy(update={
        "terminal_action": "proposal",
    })

    reply, normalized = _normalize_unpersisted_proposal_result(
        reply="这是待确认的调整提案。",
        execution_trace=trace,
        proposal_reference=None,
        intent_domain="workout_plan",
        request_kind="assessment",
    )

    assert "还没有生成可确认的训练计划提案" in reply
    assert "计划没有改动" in reply
    assert normalized.terminal_action == "answer"


def test_explicit_plan_adjustment_proposal_uses_feature_gated_planned_mode():
    resolution = IntentResolution(
        primary_intent="plan_query",
        resolved_query=(
            "请把当前训练计划周期从6周延长到8周，其他内容保持不变，"
            "并生成待确认提案。"
        ),
        subtasks=[
            "读取当前训练计划",
            "根据用户明确范围形成待确认的训练计划调整提案",
        ],
        confidence=0.95,
    )

    enabled_mode, enabled_reasons = select_execution_mode(
        resolution,
        ["plan.get_active"],
        proposal_creation_enabled=True,
    )
    disabled_mode, disabled_reasons = select_execution_mode(
        resolution,
        ["plan.get_active"],
        proposal_creation_enabled=False,
    )

    assert enabled_mode == "planned"
    assert enabled_reasons == ["explicit_plan_adjustment_proposal"]
    assert disabled_mode == "direct"
    assert disabled_reasons == ["single_goal_or_tool"]


def test_initial_trace_records_intent_attempts_and_rules_fallback():
    resolution = IntentResolution(
        primary_intent="workout_history_query",
        resolved_query="查询训练历史",
        subtasks=["查询训练历史"],
        confidence=0.9,
    )
    outcome = IntentResolverOutcome(
        resolution=resolution,
        source="rules",
        attempt_count=1,
        fallback_reason="model_timeout",
        error_category="TimeoutError",
        latency_ms=125,
        attempt_timings=(IntentAttemptTiming(
            attempt=1,
            latency_ms=124,
            status="error",
            error_category="TimeoutError",
        ),),
    )

    trace = build_initial_execution_trace(
        resolution,
        ["workout.list_history"],
        outcome,
    )

    assert [item.source for item in trace.stage_timings] == [
        "model",
        "rules",
    ]
    assert trace.stage_timings[0].latency_ms == 124
    assert trace.stage_timings[0].error_category == "TimeoutError"
    assert trace.stage_timings[1].attempt == 0
    assert trace.stage_timings[1].latency_ms == 0


def test_stage_timing_accepts_privacy_safe_model_size_metrics():
    resolution = IntentResolution(
        primary_intent="profile_query",
        resolved_query="查询训练资料",
        subtasks=["读取训练资料"],
        confidence=0.9,
    )
    trace = build_initial_execution_trace(
        resolution,
        ["profile.get_summary"],
    )

    trace = add_stage_timing(
        trace,
        stage="finalizer",
        source="model",
        status="success",
        latency_ms=2500,
        input_chars=1800,
        output_chars=120,
        input_tokens=600,
        output_tokens=80,
        finish_reason="stop",
    )

    timing = trace.stage_timings[-1]
    assert timing.input_chars == 1800
    assert timing.output_chars == 120
    assert timing.input_tokens == 600
    assert timing.output_tokens == 80
    assert timing.finish_reason == "stop"


def test_completed_trace_records_real_tool_actions_and_sanitized_observations():
    resolution = IntentResolution(
        primary_intent="profile_query",
        resolved_query="查询我的训练目标",
        subtasks=["读取训练资料"],
        confidence=0.9,
    )
    trace = build_initial_execution_trace(
        resolution,
        ["profile.get_summary"],
    )
    result = {
        "messages": [
            HumanMessage(content="我的训练目标是什么？"),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "profile_get_summary",
                    "args": {},
                    "id": "trace-profile-call",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({
                    "found": True,
                    "primary_goal": "增肌",
                    "training_days_per_week": 3,
                }),
                tool_call_id="trace-profile-call",
                name="profile_get_summary",
            ),
            AIMessage(content="你当前的目标是增肌。"),
        ]
    }

    completed = complete_execution_trace(
        trace,
        result,
        summarize_observation=_audit_result_summary,
    )

    assert completed.status == "completed"
    assert completed.execution_mode == "direct"
    assert completed.terminal_action == "answer"
    assert completed.budget_usage.model_calls == 2
    assert completed.budget_usage.tool_calls == 1
    assert completed.actions[0].tool_id == "profile.get_summary"
    assert completed.actions[0].status == "completed"
    assert completed.observations[0].status == "success"
    assert "primary_goal" in completed.observations[0].summary["fields_returned"]
    assert "增肌" not in str(completed.observations[0].summary)
    assert len(completed.observations[0].result_fingerprint) == 64
    assert "profile.get_summary.primary_goal" in (
        completed.observations[0].fact_keys
    )


def test_real_runtime_trace_can_be_scored_without_fixed_tool_order():
    dataset = load_multistep_dataset()
    case = next(
        item
        for item in dataset.cases
        if item.id == "active_session_resume_when_absent"
    )
    resolution = IntentResolution(
        primary_intent="active_workout_query",
        resolved_query=case.message,
        expanded_intents=["next_workout_query"],
        subtasks=["检查活动训练", "根据结果决定是否查询下一练"],
        confidence=0.9,
    )
    trace = build_initial_execution_trace(resolution, case.candidate_tools)
    active_result = next(
        stub.result
        for stub in case.tool_stubs
        if stub.tool == "workout.get_active_session"
    )
    next_result = next(
        stub.result
        for stub in case.tool_stubs
        if stub.tool == "workout.get_next"
    )
    result = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "workout_get_active_session",
                    "args": {},
                    "id": "active-call",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps(active_result),
                tool_call_id="active-call",
                name="workout_get_active_session",
            ),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "workout_get_next",
                    "args": {},
                    "id": "next-call",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps(next_result),
                tool_call_id="next-call",
                name="workout_get_next",
            ),
            AIMessage(content="当前没有进行中的训练，今天可以进行下一练。"),
        ]
    }
    completed = complete_execution_trace(
        trace,
        result,
        summarize_observation=_audit_result_summary,
    )

    score = score_runtime_execution_trace(case, completed)

    assert score.deterministic_pass is True
    assert score.required_tool_group_recall == 1
    assert score.required_fact_coverage == 1


def test_runtime_trace_does_not_award_facts_when_observation_differs():
    dataset = load_multistep_dataset()
    case = next(
        item
        for item in dataset.cases
        if item.id == "simple_next_workout_stays_direct"
    )
    resolution = IntentResolution(
        primary_intent="next_workout_query",
        resolved_query=case.message,
        subtasks=["查询下一练"],
        confidence=0.9,
    )
    trace = build_initial_execution_trace(resolution, case.candidate_tools)
    result = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "workout_get_next",
                    "args": {},
                    "id": "mismatched-next-call",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({"found": False, "reason": "no_active_plan"}),
                tool_call_id="mismatched-next-call",
                name="workout_get_next",
            ),
            AIMessage(content="当前没有活动计划。"),
        ]
    }
    completed = complete_execution_trace(
        trace,
        result,
        summarize_observation=_audit_result_summary,
    )

    score = score_runtime_execution_trace(case, completed)

    assert score.required_tool_group_recall == 1
    assert score.required_fact_coverage == 0
    assert score.deterministic_pass is False
