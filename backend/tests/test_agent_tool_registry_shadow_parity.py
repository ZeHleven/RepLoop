from __future__ import annotations

import logging
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain.tools import tool
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr
from sqlalchemy import select

from app.config import settings
from app.models.agent import AgentToolCall
from app.schemas.agent_planning import (
    FinalResponse,
    MicroPlan,
    MicroPlanStep,
    PlannedToolAction,
)
from app.services.agent_controller import ToolAuditEvent, execute_planned_agent
from app.services.agent_intent import (
    IntentResolution,
    IntentResolverOutcome,
    normalize_resolution,
    resolve_intent,
    route_tools,
)
from app.services.agent_runtime import _audit_result_summary
from app.services.agent_tool_registry_read_enforcement import (
    REGISTRY_READ_AUTHORITY_LOG_PREFIX,
    apply_optional_registry_read_enforcement,
)
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
)
from app.services.agent_tools import (
    NoArguments,
    WorkoutHistoryArguments,
    WorkoutProgressArguments,
)
from app.services.agent_trace import build_initial_execution_trace


class ToolAwareFakeChatModel(FakeMessagesListChatModel):
    _bound_tool_names: list[str] = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **_kwargs):
        self._bound_tool_names = [item.name for item in tools]
        return self


_READ_TOOL_DIRECT_CASES = (
    (
        "profile.get_summary",
        "profile_get_summary",
        "我的训练目标是什么？",
    ),
    (
        "health.get_screening_summary",
        "health_get_screening_summary",
        "我的伤病情况是什么？",
    ),
    (
        "plan.get_active",
        "plan_get_active",
        "我的训练计划是什么？",
    ),
    (
        "workout.get_next",
        "workout_get_next",
        "我的下一练是什么？",
    ),
    (
        "workout.get_active_session",
        "workout_get_active_session",
        "查看我进行中的训练。",
    ),
    (
        "workout.list_history",
        "workout_list_history",
        "查看我的训练历史。",
    ),
    (
        "workout.get_progress",
        "workout_get_progress",
        "查看我的训练进度。",
    ),
)


async def _token(client, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def _direct_read_model(
    tool_name: str,
    *,
    reply: str = "读取完成。",
) -> ToolAwareFakeChatModel:
    return ToolAwareFakeChatModel(responses=[
        AIMessage(
            content="",
            tool_calls=[{
                "name": tool_name,
                "args": {},
                "id": f"registry-parity-{tool_name}-call",
                "type": "tool_call",
            }],
        ),
        AIMessage(content=reply),
    ])


def _direct_model() -> ToolAwareFakeChatModel:
    return _direct_read_model(
        "profile_get_summary",
        reply="当前还没有保存训练资料。",
    )


def _policy(plan: MicroPlan, reply: str) -> SimpleNamespace:
    return SimpleNamespace(
        create_plan=AsyncMock(return_value=plan),
        decide_step=AsyncMock(),
        revise_plan=AsyncMock(),
        finalize=AsyncMock(return_value=FinalResponse(
            terminal_action="answer",
            reply=reply,
        )),
    )


async def _run_chat(
    client,
    db_session,
    *,
    email: str,
    message: str,
    shadow_enabled: bool,
    sample_rate: float,
    persist_trace: bool,
    model: ToolAwareFakeChatModel | None = None,
    policy: SimpleNamespace | None = None,
    access_token: str | None = None,
    parallel_session_factory=None,
    registry_enforcement_enabled: bool = False,
) -> dict[str, Any]:
    token = access_token or await _token(client, email)
    headers = {"Authorization": f"Bearer {token}"}

    with ExitStack() as stack:
        async def deterministic_model_outcome(current_message, **_kwargs):
            return IntentResolverOutcome(
                resolution=normalize_resolution(
                    current_message,
                    resolve_intent(current_message),
                ),
                source="model",
                attempt_count=1,
            )

        stack.enter_context(patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=deterministic_model_outcome,
        ))
        stack.enter_context(patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_ENABLED",
            shadow_enabled,
        ))
        stack.enter_context(patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE",
            sample_rate,
        ))
        stack.enter_context(patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE",
            persist_trace,
        ))
        stack.enter_context(patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED",
            registry_enforcement_enabled,
        ))
        stack.enter_context(patch(
            "app.services.agent_runtime._build_model",
            return_value=model if model is not None else object(),
        ))
        if policy is not None:
            stack.enter_context(patch(
                "app.services.agent_controller.ModelPlanningPolicy",
                return_value=policy,
            ))
        if parallel_session_factory is not None:
            stack.enter_context(patch(
                "app.services.agent_runtime.AsyncSessionLocal",
                parallel_session_factory,
            ))
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": message},
            headers=headers,
        )

    assert response.status_code == 200
    response_body = response.json()
    run_response = await client.get(
        f"/api/v1/agent/runs/{response_body['run_id']}",
        headers=headers,
    )
    assert run_response.status_code == 200
    run_body = run_response.json()
    tool_audits = list((await db_session.execute(
        select(AgentToolCall).where(
            AgentToolCall.run_id == response_body["run_id"]
        )
    )).scalars().all())

    return {
        "response": response_body,
        "run": run_body,
        "tool_audits": tool_audits,
        "policy": policy,
        "model": model,
    }


def _trace_snapshot(trace: dict[str, Any]) -> dict[str, Any]:
    actions = [{
        "sequence": item["sequence"],
        "step_id": item.get("step_id"),
        "plan_version": item.get("plan_version"),
        "tool_id": item["tool_id"],
        "arguments": item["arguments"],
        "status": item["status"],
    } for item in trace["actions"]]
    observations = [{
        "sequence": item["sequence"],
        "action_sequence": item["action_sequence"],
        "tool_id": item["tool_id"],
        "status": item["status"],
        "summary": item["summary"],
        "result_fingerprint": item["result_fingerprint"],
        "fact_keys": item["fact_keys"],
    } for item in trace["observations"]]
    return {
        "status": trace["status"],
        "execution_mode": trace["execution_mode"],
        "mode_reasons": trace["mode_reasons"],
        "plan": trace["plan"],
        "actions": actions,
        "observations": observations,
        "stage_timings": [{
            "stage": item["stage"],
            "source": item["source"],
            "status": item["status"],
            "error_category": item.get("error_category"),
        } for item in trace["stage_timings"]],
        "budget_usage": trace["budget_usage"],
        "terminal_action": trace["terminal_action"],
        "termination_reason": trace["termination_reason"],
    }


def _policy_call_snapshot(policy: SimpleNamespace | None) -> dict | None:
    if policy is None:
        return None
    return {
        "planner": policy.create_plan.await_count,
        "executor": policy.decide_step.await_count,
        "replanner": policy.revise_plan.await_count,
        "finalizer": policy.finalize.await_count,
    }


def _behavior_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    response = result["response"]
    run = result["run"]
    trace = run["execution_trace"]
    audits_by_call_id = {
        item.call_id: item for item in result["tool_audits"]
    }

    audits = []
    for action in trace["actions"]:
        audit = audits_by_call_id.get(action.get("call_id"))
        if audit is None:
            continue
        audits.append({
            "tool_name": audit.tool_name,
            "arguments_data": audit.arguments_data,
            "result_data": audit.result_data,
            "status": audit.status,
            "error_code": audit.error_code,
        })

    return {
        "response": {
            "reply": response["reply"],
            "cards": response["cards"],
        },
        "run": {
            "status": run["status"],
            "execution_mode": run["execution_mode"],
            "primary_intent": run["primary_intent"],
            "expanded_intents": run["expanded_intents"],
            "subtasks": run["subtasks"],
            "tool_allowlist": run["tool_allowlist"],
        },
        "trace": _trace_snapshot(trace),
        "tool_audits": audits,
        "policy_calls": _policy_call_snapshot(result["policy"]),
        "bound_model_tools": (
            result["model"]._bound_tool_names
            if result["model"] is not None
            else None
        ),
    }


def _controller_behavior_snapshot(
    result,
    policy: SimpleNamespace,
    audits: list[ToolAuditEvent],
) -> dict[str, Any]:
    return {
        "reply": result.reply,
        "cards": result.cards,
        "missing_slots": result.missing_slots,
        "trace": _trace_snapshot(
            result.execution_trace.model_dump(mode="json")
        ),
        "tool_audits": [{
            "tool_id": item.tool_id,
            "arguments": item.arguments,
            "result_summary": item.result_summary,
            "status": item.status,
            "error_code": item.error_code,
        } for item in audits],
        "policy_calls": _policy_call_snapshot(policy),
    }


def _assert_persisted_shadow_report(result: dict[str, Any]) -> None:
    trace = result["run"]["execution_trace"]
    report = trace["tool_registry_shadow"]

    assert trace["trace_version"] == "1.1"
    assert report["mode"] == "shadow"
    assert not {
        code
        for check in report["checks"]
        for code in check["mismatch_codes"]
    }
    assert all(
        check["status"] in {"match", "skipped"}
        for check in report["checks"]
    )


@pytest.mark.asyncio
async def test_direct_run_behavior_is_identical_across_shadow_modes(
    client,
    db_session,
):
    message = "我的训练目标是什么？"
    token = await _token(client, "shadow-parity-direct@example.com")
    baseline = await _run_chat(
        client,
        db_session,
        email="shadow-parity-direct-baseline@example.com",
        message=message,
        shadow_enabled=False,
        sample_rate=1.0,
        persist_trace=True,
        model=_direct_model(),
        access_token=token,
    )
    variants = [
        (
            "sample-miss",
            0.0,
            True,
            False,
        ),
        (
            "sampled-not-persisted",
            1.0,
            False,
            False,
        ),
        (
            "sampled-persisted",
            1.0,
            True,
            True,
        ),
    ]

    baseline_snapshot = _behavior_snapshot(baseline)
    assert baseline_snapshot["trace"]["budget_usage"] == {
        "model_calls": 2,
        "tool_calls": 1,
        "plan_steps": 1,
        "replans": 0,
    }
    assert baseline_snapshot["tool_audits"][0]["tool_name"] == (
        "profile.get_summary"
    )

    for label, sample_rate, persist_trace, report_expected in variants:
        shadowed = await _run_chat(
            client,
            db_session,
            email=f"shadow-parity-direct-{label}@example.com",
            message=message,
            shadow_enabled=True,
            sample_rate=sample_rate,
            persist_trace=persist_trace,
            model=_direct_model(),
            access_token=token,
        )

        assert _behavior_snapshot(shadowed) == baseline_snapshot
        trace = shadowed["run"]["execution_trace"]
        if report_expected:
            _assert_persisted_shadow_report(shadowed)
        else:
            assert trace["trace_version"] == "1.0"
            assert "tool_registry_shadow" not in trace


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_id", "tool_name", "message"),
    _READ_TOOL_DIRECT_CASES,
)
async def test_each_read_tool_direct_behavior_is_identical_when_enforced(
    client,
    db_session,
    tool_id: str,
    tool_name: str,
    message: str,
):
    email_key = tool_id.replace(".", "-")
    token = await _token(client, f"registry-parity-{email_key}@example.com")
    baseline = await _run_chat(
        client,
        db_session,
        email=f"registry-parity-{email_key}-baseline@example.com",
        message=message,
        shadow_enabled=False,
        sample_rate=0.0,
        persist_trace=False,
        model=_direct_read_model(tool_name),
        access_token=token,
    )
    enforced = await _run_chat(
        client,
        db_session,
        email=f"registry-parity-{email_key}-enforced@example.com",
        message=message,
        shadow_enabled=False,
        sample_rate=0.0,
        persist_trace=False,
        model=_direct_read_model(tool_name),
        access_token=token,
        registry_enforcement_enabled=True,
    )

    assert _behavior_snapshot(enforced) == _behavior_snapshot(baseline)
    snapshot = _behavior_snapshot(enforced)
    assert snapshot["run"]["tool_allowlist"] == [tool_id]
    assert [item["tool_id"] for item in snapshot["trace"]["actions"]] == [
        tool_id
    ]
    assert snapshot["trace"]["budget_usage"] == {
        "model_calls": 2,
        "tool_calls": 1,
        "plan_steps": 1,
        "replans": 0,
    }
    assert enforced["run"]["execution_trace"]["trace_version"] == "1.0"


def test_read_tool_direct_cases_route_to_single_expected_tool():
    for tool_id, _tool_name, message in _READ_TOOL_DIRECT_CASES:
        assert route_tools(resolve_intent(message)) == [tool_id]


@pytest.mark.asyncio
async def test_parallel_planned_run_behavior_is_unchanged_by_shadow(
    client,
    db_session,
    session_factory,
):
    message = "结合我的资料，看看当前计划是什么"
    plan = MicroPlan(
        goal="结合资料查看当前计划",
        steps=[MicroPlanStep(
            objective="并行读取当前计划和训练资料",
            candidate_tools=["plan.get_active", "profile.get_summary"],
            execution_strategy="parallel_read",
            completion_policy="after_all_observations",
            planned_actions=[
                PlannedToolAction(tool_id="plan.get_active", arguments={}),
                PlannedToolAction(
                    tool_id="profile.get_summary",
                    arguments={},
                ),
            ],
            success_signal="计划和资料观察均已返回",
        )],
    )
    reply = "你目前没有活动计划，也没有保存完整训练资料。"
    baseline_policy = _policy(plan, reply)
    shadow_policy = _policy(plan, reply)
    enforced_policy = _policy(plan, reply)
    token = await _token(client, "shadow-parity-parallel@example.com")

    baseline = await _run_chat(
        client,
        db_session,
        email="shadow-parity-parallel-baseline@example.com",
        message=message,
        shadow_enabled=False,
        sample_rate=0.0,
        persist_trace=False,
        policy=baseline_policy,
        access_token=token,
        parallel_session_factory=session_factory,
    )
    shadowed = await _run_chat(
        client,
        db_session,
        email="shadow-parity-parallel-enabled@example.com",
        message=message,
        shadow_enabled=True,
        sample_rate=1.0,
        persist_trace=True,
        policy=shadow_policy,
        access_token=token,
        parallel_session_factory=session_factory,
    )
    enforced = await _run_chat(
        client,
        db_session,
        email="registry-parity-parallel-enforced@example.com",
        message=message,
        shadow_enabled=True,
        sample_rate=1.0,
        persist_trace=True,
        policy=enforced_policy,
        access_token=token,
        parallel_session_factory=session_factory,
        registry_enforcement_enabled=True,
    )

    assert _behavior_snapshot(shadowed) == _behavior_snapshot(baseline)
    assert _behavior_snapshot(enforced) == _behavior_snapshot(baseline)
    snapshot = _behavior_snapshot(shadowed)
    assert [item["tool_id"] for item in snapshot["trace"]["actions"]] == [
        "plan.get_active",
        "profile.get_summary",
    ]
    assert snapshot["trace"]["budget_usage"] == {
        "model_calls": 2,
        "tool_calls": 2,
        "plan_steps": 1,
        "replans": 0,
    }
    assert snapshot["policy_calls"] == {
        "planner": 1,
        "executor": 0,
        "replanner": 0,
        "finalizer": 1,
    }
    _assert_persisted_shadow_report(shadowed)
    _assert_persisted_shadow_report(enforced)
    assert shadowed["run"]["execution_trace"][
        "tool_registry_shadow"
    ]["status"] == "match"
    assert enforced["run"]["execution_trace"][
        "tool_registry_shadow"
    ]["status"] == "match"


@pytest.mark.asyncio
async def test_conditional_fallback_behavior_is_unchanged_by_shadow():
    calls: list[str] = []

    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        calls.append("profile.get_summary")
        return {"found": True, "onboarding_completed": True}

    @tool(
        "workout_get_active_session",
        args_schema=NoArguments,
        description="读取活动训练",
    )
    async def active_session():
        calls.append("workout.get_active_session")
        return {"found": False}

    @tool(
        "workout_get_next",
        args_schema=NoArguments,
        description="读取下一练",
    )
    async def next_workout():
        calls.append("workout.get_next")
        return {"found": False, "reason": "no_active_plan"}

    resolution = IntentResolution(
        primary_intent="active_workout_query",
        evidence_requirements=[
            "active_workout_session",
            "next_workout",
            "profile_summary",
        ],
        resolved_query="结合训练资料判断继续当前训练还是开始下一练",
        expanded_intents=["next_workout_query", "profile_query"],
        subtasks=["检查活动训练", "读取训练资料", "必要时查询下一练"],
        confidence=0.9,
    )
    allowlist = route_tools(resolution)
    primary_tools = [
        "workout.get_active_session",
        "profile.get_summary",
    ]
    plan = MicroPlan(
        goal="判断继续当前训练还是开始下一练",
        steps=[
            MicroPlanStep(
                objective="并行读取活动训练和训练资料",
                candidate_tools=primary_tools,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(tool_id=item, arguments={})
                    for item in primary_tools
                ],
                success_signal="活动训练或条件替代证据已返回",
            ),
            MicroPlanStep(
                objective="没有活动训练时读取下一练",
                candidate_tools=["workout.get_next"],
                execution_strategy="direct",
                success_signal="取得下一练条件替代证据",
            ),
        ],
    )
    reply = "当前没有进行中的训练，也没有可用的下一练。"

    async def run(shadow_session=None, *, enforcement_enabled=False):
        policy = _policy(plan, reply)
        audits: list[ToolAuditEvent] = []
        effective_allowlist = list(
            apply_optional_registry_read_enforcement(
                resolution=resolution,
                legacy_tool_ids=allowlist,
                enabled=enforcement_enabled,
                run_id="registry-parity-fallback-run",
            ).tool_allowlist
        )

        async def sink(_trace, audit):
            if audit is not None:
                audits.append(audit)

        result = await execute_planned_agent(
            db=None,
            user_id="shadow-parity-fallback-user",
            run_id="shadow-parity-fallback-run",
            model=None,
            goal=resolution.resolved_query,
            subtasks=resolution.subtasks,
            tool_allowlist=effective_allowlist,
            initial_trace=build_initial_execution_trace(
                resolution,
                effective_allowlist,
            ),
            summarize_observation=_audit_result_summary,
            event_sink=sink,
            policy=policy,
            tools=[active_session, next_workout, profile],
            shadow_session=shadow_session,
        )
        if shadow_session is not None:
            shadow_session.record_final_observations(result.execution_trace)
        return result, policy, audits

    baseline, baseline_policy, baseline_audits = await run()
    baseline_calls = list(calls)
    calls.clear()
    session = ToolRegistryShadowSession(sample_bucket=37)
    session.record_route(resolution, allowlist)
    shadowed, shadow_policy, shadow_audits = await run(session)
    shadow_calls = list(calls)
    calls.clear()
    enforced, enforced_policy, enforced_audits = await run(
        enforcement_enabled=True,
    )

    assert _controller_behavior_snapshot(
        shadowed,
        shadow_policy,
        shadow_audits,
    ) == _controller_behavior_snapshot(
        baseline,
        baseline_policy,
        baseline_audits,
    )
    assert _controller_behavior_snapshot(
        enforced,
        enforced_policy,
        enforced_audits,
    ) == _controller_behavior_snapshot(
        baseline,
        baseline_policy,
        baseline_audits,
    )
    assert calls == shadow_calls == baseline_calls == [
        "workout.get_active_session",
        "profile.get_summary",
        "workout.get_next",
    ]
    snapshot = _controller_behavior_snapshot(
        shadowed,
        shadow_policy,
        shadow_audits,
    )
    assert snapshot["trace"]["budget_usage"] == {
        "model_calls": 2,
        "tool_calls": 3,
        "plan_steps": 2,
        "replans": 0,
    }
    assert snapshot["policy_calls"]["executor"] == 0
    report = session.build_report().model_dump(mode="json")
    assert report["status"] == "match"
    assert next(
        check for check in report["checks"]
        if check["check_type"] == "conditional_evidence"
    )["status"] == "match"


@pytest.mark.asyncio
async def test_tool_error_fallback_behavior_is_unchanged_by_shadow():
    calls: list[str] = []

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取当前计划",
    )
    async def active_plan():
        calls.append("plan.get_active")
        return {"found": True, "name": "测试计划"}

    @tool(
        "workout_get_progress",
        args_schema=WorkoutProgressArguments,
        description="读取训练进度",
    )
    async def progress(weeks: int = 8):
        calls.append("workout.get_progress")
        raise TimeoutError(f"fixture timeout after {weeks} weeks")

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        calls.append("workout.list_history")
        return {"count": 0, "sessions": [], "limit": limit}

    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        calls.append("profile.get_summary")
        return {"found": True, "onboarding_completed": True}

    resolution = IntentResolution(
        primary_intent="plan_query",
        evidence_requirements=[
            "active_plan",
            "workout_progress",
            "workout_history",
            "profile_summary",
        ],
        resolved_query="结合资料、计划和训练证据判断当前计划",
        expanded_intents=[
            "workout_progress_query",
            "workout_history_query",
            "profile_query",
        ],
        subtasks=["读取计划", "读取进度", "读取资料"],
        confidence=0.9,
    )
    allowlist = route_tools(resolution)
    primary_tools = [
        "plan.get_active",
        "workout.get_progress",
        "profile.get_summary",
    ]
    plan = MicroPlan(
        goal=resolution.resolved_query,
        steps=[
            MicroPlanStep(
                objective="并行读取计划、进度和资料",
                candidate_tools=primary_tools,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(tool_id=item, arguments={})
                    for item in primary_tools
                ],
                success_signal="三项主证据均已解析",
            ),
            MicroPlanStep(
                objective="进度失败时读取训练历史",
                candidate_tools=["workout.list_history"],
                execution_strategy="direct",
                success_signal="取得训练历史替代证据",
            ),
        ],
    )
    reply = "训练进度暂不可用，已使用训练历史完成回答。"

    async def run(shadow_session=None, *, enforcement_enabled=False):
        policy = _policy(plan, reply)
        audits: list[ToolAuditEvent] = []
        effective_allowlist = list(
            apply_optional_registry_read_enforcement(
                resolution=resolution,
                legacy_tool_ids=allowlist,
                enabled=enforcement_enabled,
                run_id="registry-parity-tool-error-run",
            ).tool_allowlist
        )

        async def sink(_trace, audit):
            if audit is not None:
                audits.append(audit)

        result = await execute_planned_agent(
            db=None,
            user_id="shadow-parity-tool-error-user",
            run_id="shadow-parity-tool-error-run",
            model=None,
            goal=resolution.resolved_query,
            subtasks=resolution.subtasks,
            tool_allowlist=effective_allowlist,
            initial_trace=build_initial_execution_trace(
                resolution,
                effective_allowlist,
            ),
            summarize_observation=_audit_result_summary,
            event_sink=sink,
            policy=policy,
            tools=[active_plan, progress, history, profile],
            shadow_session=shadow_session,
        )
        if shadow_session is not None:
            shadow_session.record_final_observations(result.execution_trace)
        return result, policy, audits

    baseline, baseline_policy, baseline_audits = await run()
    baseline_calls = list(calls)
    calls.clear()
    session = ToolRegistryShadowSession(sample_bucket=41)
    session.record_route(resolution, allowlist)
    shadowed, shadow_policy, shadow_audits = await run(session)
    shadow_calls = list(calls)
    calls.clear()
    enforced, enforced_policy, enforced_audits = await run(
        enforcement_enabled=True,
    )

    assert _controller_behavior_snapshot(
        shadowed,
        shadow_policy,
        shadow_audits,
    ) == _controller_behavior_snapshot(
        baseline,
        baseline_policy,
        baseline_audits,
    )
    assert _controller_behavior_snapshot(
        enforced,
        enforced_policy,
        enforced_audits,
    ) == _controller_behavior_snapshot(
        baseline,
        baseline_policy,
        baseline_audits,
    )
    assert calls == shadow_calls == baseline_calls == [
        "plan.get_active",
        "workout.get_progress",
        "profile.get_summary",
        "workout.list_history",
    ]
    snapshot = _controller_behavior_snapshot(
        shadowed,
        shadow_policy,
        shadow_audits,
    )
    assert [
        item["status"] for item in snapshot["trace"]["observations"]
    ] == ["success", "error", "success", "success"]
    assert snapshot["trace"]["budget_usage"] == {
        "model_calls": 2,
        "tool_calls": 4,
        "plan_steps": 2,
        "replans": 0,
    }
    assert snapshot["policy_calls"]["executor"] == 0
    report = session.build_report().model_dump(mode="json")
    assert report["status"] == "match"
    assert next(
        check for check in report["checks"]
        if check["check_type"] == "conditional_evidence"
    )["status"] == "match"


@pytest.mark.asyncio
async def test_shadow_comparator_error_does_not_change_run_behavior(
    client,
    db_session,
):
    token = await _token(client, "shadow-parity-error@example.com")
    baseline = await _run_chat(
        client,
        db_session,
        email="shadow-parity-error-baseline@example.com",
        message="我的训练目标是什么？",
        shadow_enabled=False,
        sample_rate=0.0,
        persist_trace=False,
        model=_direct_model(),
        access_token=token,
    )

    with patch(
        "app.services.agent_tool_registry_shadow_trace."
        "registry_observation_semantics_fact",
        side_effect=RuntimeError("private comparator detail"),
    ):
        shadowed = await _run_chat(
            client,
            db_session,
            email="shadow-parity-error-enabled@example.com",
            message="我的训练目标是什么？",
            shadow_enabled=True,
            sample_rate=1.0,
            persist_trace=True,
            model=_direct_model(),
            access_token=token,
        )

    assert _behavior_snapshot(shadowed) == _behavior_snapshot(baseline)
    report = shadowed["run"]["execution_trace"]["tool_registry_shadow"]
    error_check = next(
        check for check in report["checks"]
        if check["check_type"] == "observation_semantics"
    )
    assert report["status"] == "error"
    assert error_check["status"] == "error"
    assert error_check["mismatch_codes"] == ["shadow_internal_error"]
    assert error_check["error_category"] == "shadow_fact_builder_error"
    assert "private comparator detail" not in str(report)


@pytest.mark.asyncio
async def test_enforcement_failure_and_flag_rollback_preserve_run_behavior(
    client,
    db_session,
    caplog,
):
    caplog.set_level(
        logging.WARNING,
        logger="app.services.agent_tool_registry_read_enforcement",
    )
    token = await _token(client, "registry-rollback@example.com")
    baseline = await _run_chat(
        client,
        db_session,
        email="registry-rollback-baseline@example.com",
        message="我的训练目标是什么？",
        shadow_enabled=False,
        sample_rate=0.0,
        persist_trace=False,
        model=_direct_model(),
        access_token=token,
    )

    with patch(
        "app.services.agent_tool_registry_read_enforcement."
        "route_registry_read_tool_ids",
        side_effect=RuntimeError("private registry projection detail"),
    ):
        fallback = await _run_chat(
            client,
            db_session,
            email="registry-rollback-fallback@example.com",
            message="我的训练目标是什么？",
            shadow_enabled=False,
            sample_rate=0.0,
            persist_trace=False,
            model=_direct_model(),
            access_token=token,
            registry_enforcement_enabled=True,
        )
        rolled_back = await _run_chat(
            client,
            db_session,
            email="registry-rollback-disabled@example.com",
            message="我的训练目标是什么？",
            shadow_enabled=False,
            sample_rate=0.0,
            persist_trace=False,
            model=_direct_model(),
            access_token=token,
            registry_enforcement_enabled=False,
        )

    baseline_snapshot = _behavior_snapshot(baseline)
    assert _behavior_snapshot(fallback) == baseline_snapshot
    assert _behavior_snapshot(rolled_back) == baseline_snapshot
    assert baseline_snapshot["run"]["status"] == "completed"
    assert baseline_snapshot["trace"]["budget_usage"] == {
        "model_calls": 2,
        "tool_calls": 1,
        "plan_steps": 1,
        "replans": 0,
    }
    decision_logs = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith(
            REGISTRY_READ_AUTHORITY_LOG_PREFIX
        )
    ]
    assert len(decision_logs) == 1
    assert '"authority_mode":"legacy_fallback"' in decision_logs[0]
    assert '"reason_codes":["registry_internal_error"]' in decision_logs[0]
    assert "private registry projection detail" not in decision_logs[0]
