import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select

from app.config import settings
from app.models.agent import AgentConversation, AgentRun, AgentToolCall
from app.models.user import User
from app.schemas.agent_planning import (
    ExecutorDecision,
    FinalResponse,
    MicroPlan,
    MicroPlanStep,
    PlannedToolAction,
)
from app.services.agent_intent import (
    IntentResolution,
    IntentResolverOutcome,
    normalize_resolution,
    resolve_intent,
)


@pytest.fixture(autouse=True)
def disable_intent_model_for_router_tests():
    async def deterministic_model_outcome(message, **_kwargs):
        return IntentResolverOutcome(
            resolution=normalize_resolution(message, resolve_intent(message)),
            source="model",
            attempt_count=1,
        )

    with patch(
        "app.services.agent_runtime.resolve_intent_with_fallback",
        new=deterministic_model_outcome,
    ):
        yield


async def _token(client, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_agent_chat_persists_conversation_run_messages_and_tool_audit(
    client,
    db_session,
):
    token = await _token(client, "agent-v1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    mocked_result = {
        "messages": [
            HumanMessage(content="我的训练目标是什么？"),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "profile_get_summary",
                    "args": {},
                    "id": "call-profile-1",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({"found": True, "primary_goal": "增肌"}),
                tool_call_id="call-profile-1",
                name="profile_get_summary",
            ),
            AIMessage(content="你当前保存的训练目标是增肌。"),
        ]
    }

    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=AsyncMock(return_value=mocked_result),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我的训练目标是什么？"},
            headers=headers,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == "你当前保存的训练目标是增肌。"
    assert body["cards"][0]["type"] == "profile.get_summary"

    conversations = await client.get(
        "/api/v1/agent/conversations",
        headers=headers,
    )
    saved_conversation = next(
        item for item in conversations.json()
        if item["id"] == body["conversation_id"]
    )
    assert saved_conversation["summary"] == "你当前保存的训练目标是增肌。"

    messages = await client.get(
        f"/api/v1/agent/conversations/{body['conversation_id']}/messages",
        headers=headers,
    )
    assert [item["role"] for item in messages.json()] == ["user", "assistant"]

    run_response = await client.get(
        f"/api/v1/agent/runs/{body['run_id']}",
        headers=headers,
    )
    assert run_response.status_code == 200
    assert run_response.json()["tool_allowlist"] == ["profile.get_summary"]
    assert run_response.json()["intent_source"] == "model"
    assert run_response.json()["intent_confidence"] == 0.9
    assert run_response.json()["duration_ms"] is not None
    assert run_response.json()["execution_mode"] == "direct"
    execution_trace = run_response.json()["execution_trace"]
    assert execution_trace["status"] == "completed"
    assert execution_trace["terminal_action"] == "answer"
    assert execution_trace["actions"][0]["tool_id"] == "profile.get_summary"
    assert execution_trace["observations"][0]["status"] == "success"
    assert execution_trace["trace_version"] == "1.0"
    assert "tool_registry_shadow" not in execution_trace

    tool_call = await db_session.scalar(
        select(AgentToolCall).where(AgentToolCall.run_id == body["run_id"])
    )
    assert tool_call is not None
    assert tool_call.tool_name == "profile.get_summary"
    assert "primary_goal" in tool_call.result_data["fields_returned"]
    assert "增肌" not in str(tool_call.result_data)


@pytest.mark.asyncio
async def test_optional_read_enforcement_keeps_matching_runtime_allowlist(
    client,
):
    token = await _token(client, "agent-read-enforce@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    mocked_result = {"messages": [AIMessage(content="已读取资料。")]}
    invoke = AsyncMock(return_value=mocked_result)

    with (
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED",
            True,
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=invoke,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我的训练目标是什么？"},
            headers=headers,
        )

    assert response.status_code == 200
    assert invoke.await_args.kwargs["tool_allowlist"] == [
        "profile.get_summary"
    ]
    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers=headers,
    )
    assert run_response.json()["tool_allowlist"] == [
        "profile.get_summary"
    ]


@pytest.mark.asyncio
async def test_sampled_shadow_report_is_optional_and_privacy_safe(client):
    token = await _token(client, "agent-shadow-trace@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    mocked_result = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "profile_get_summary",
                    "args": {},
                    "id": "shadow-profile-call",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({
                    "found": True,
                    "primary_goal": "敏感目标值",
                }),
                tool_call_id="shadow-profile-call",
                name="profile_get_summary",
            ),
            AIMessage(content="已根据资料回答。"),
        ]
    }

    with (
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_ENABLED",
            True,
        ),
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE",
            1.0,
        ),
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE",
            True,
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(return_value=mocked_result),
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我的训练目标是什么？"},
            headers=headers,
        )

    assert response.status_code == 200
    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers=headers,
    )
    trace = run_response.json()["execution_trace"]
    report = trace["tool_registry_shadow"]
    serialized_report = json.dumps(report, ensure_ascii=False)

    assert trace["trace_version"] == "1.1"
    assert report["mode"] == "shadow"
    assert report["status"] == "partial"
    assert report["checks"][0]["check_type"] == "route_allowlist"
    assert report["checks"][0]["status"] == "match"
    assert "敏感目标值" not in serialized_report
    assert "我的训练目标是什么" not in serialized_report


@pytest.mark.asyncio
async def test_agent_conversation_is_isolated_by_user(client):
    first_token = await _token(client, "agent-owner@example.com")
    second_token = await _token(client, "agent-other@example.com")
    result = {"messages": [AIMessage(content="回答")]}
    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=AsyncMock(return_value=result),
    ):
        created = await client.post(
            "/api/v1/agent/chat",
            json={"message": "普通健身问题"},
            headers={"Authorization": f"Bearer {first_token}"},
        )

    response = await client.get(
        f"/api/v1/agent/conversations/{created.json()['conversation_id']}/messages",
        headers={"Authorization": f"Bearer {second_token}"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_composite_request_persists_planned_execution_trace(
    client,
    db_session,
):
    token = await _token(client, "agent-planned-trace@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    plan = MicroPlan(
        goal="结合资料查看当前计划",
        steps=[MicroPlanStep(
            objective="并行读取当前计划和训练资料",
            candidate_tools=[
                "plan.get_active",
                "profile.get_summary",
            ],
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
    policy = SimpleNamespace(
        create_plan=AsyncMock(return_value=plan),
        decide_step=AsyncMock(),
        revise_plan=AsyncMock(),
        finalize=AsyncMock(return_value=FinalResponse(
            terminal_action="answer",
            reply="你目前没有活动计划，也没有保存完整训练资料。",
        )),
    )
    with (
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_controller.ModelPlanningPolicy",
            return_value=policy,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "结合我的资料，看看当前计划是什么"},
            headers=headers,
        )

    assert response.status_code == 200
    run = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers=headers,
    )
    trace = run.json()["execution_trace"]
    assert run.json()["execution_mode"] == "planned"
    assert trace["plan"]["planner_source"] == "model_micro_plan_v1"
    assert len(trace["plan"]["steps"]) == 1
    assert trace["plan"]["steps"][0]["execution_strategy"] == (
        "parallel_read"
    )
    assert [item["tool_id"] for item in trace["actions"]] == [
        "plan.get_active",
        "profile.get_summary",
    ]
    assert [item["status"] for item in trace["observations"]] == [
        "success",
        "success",
    ]
    assert len({item["batch_id"] for item in trace["actions"]}) == 1
    assert policy.decide_step.await_count == 0
    assert trace["budget_usage"]["model_calls"] == 2
    tool_audits = list((await db_session.execute(
        select(AgentToolCall).where(
            AgentToolCall.run_id == response.json()["run_id"]
        )
    )).scalars().all())
    assert len(tool_audits) == 2
    assert len({item.call_id for item in tool_audits}) == 2


@pytest.mark.asyncio
async def test_agent_unconfigured_model_returns_503_and_marks_run_failed(
    client,
    db_session,
):
    token = await _token(client, "agent-disabled@example.com")
    with patch.object(settings, "DEEPSEEK_API_KEY", ""):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我的当前计划是什么？"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 503
    user = await db_session.scalar(
        select(User).where(User.email == "agent-disabled@example.com")
    )
    assert user is not None
    failed_run = await db_session.scalar(
        select(AgentRun)
        .where(
            AgentRun.model_name == settings.AGENT_MODEL,
            AgentRun.user_id == user.id,
        )
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.error_code == "ai_service_error"
    assert failed_run.execution_mode == "direct"
    assert failed_run.execution_trace["status"] == "failed"
    assert failed_run.execution_trace["terminal_action"] == "failed"


@pytest.mark.asyncio
async def test_agent_run_exposes_privacy_safe_intent_error_category(client):
    token = await _token(client, "agent-intent-error-category@example.com")
    outcome = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="next_workout_query",
            resolved_query="查询下一练",
            subtasks=["查询下一练"],
            confidence=0.9,
        ),
        source="rules",
        attempt_count=2,
        fallback_reason="schema_validation_failed",
        error_category=(
            "ValidationError>ValidationError:"
            "literal_error@expanded_intents.0"
        ),
    )
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(return_value={
                "messages": [AIMessage(content="下一练查询完成。")],
            }),
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我的下一练是什么？"},
            headers={"Authorization": f"Bearer {token}"},
        )

    run = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run.json()["intent_fallback_reason"] == (
        "schema_validation_failed"
    )
    assert run.json()["intent_error_category"] == (
        "ValidationError>ValidationError:"
        "literal_error@expanded_intents.0"
    )


@pytest.mark.asyncio
async def test_untrusted_intent_fallback_stops_before_tools_or_private_reads(
    client,
):
    token = await _token(client, "agent-intent-unavailable@example.com")
    outcome = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="workout_progress_query",
            intent_domain="workout_progress",
            request_kind="assessment",
            requested_effect="read",
            resolved_query="评估近期执行情况",
            confidence=0.45,
        ),
        source="rules",
        attempt_count=2,
        fallback_reason="model_timeout",
        error_category="TimeoutError",
        understanding_failed=True,
    )
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(),
        ) as invoke_agent,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "综合我的资料给我配今天三顿饭"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "没有读取或修改你的业务数据" in response.json()["reply"]
    run = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run.json()["error_code"] == "intent_understanding_unavailable"
    assert run.json()["execution_trace"]["termination_reason"] == (
        "intent_understanding_unavailable"
    )
    invoke_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_health_red_flag_is_intercepted_before_model(client):
    token = await _token(client, "agent-red-flag@example.com")
    with patch(
        "app.services.agent_runtime.invoke_langchain_agent",
        new=AsyncMock(),
    ) as invoke_agent:
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我训练时突然胸痛，还能继续吗？"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "停止训练" in response.json()["reply"]
    run = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run.json()["risk_level"] == "high"
    assert run.json()["tool_allowlist"] == []
    assert run.json()["execution_mode"] == "safe_stop"
    assert run.json()["execution_trace"]["terminal_action"] == "safe_stop"
    invoke_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_required_clarification_does_not_call_tools_or_main_model(client):
    token = await _token(client, "agent-clarification@example.com")
    outcome = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="workout_history_query",
            missing_slots=["要查询的时间范围"],
            clarification_required=True,
            confidence=0.62,
        ),
        source="model",
        attempt_count=1,
    )
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(),
        ) as invoke_agent,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            json={"message": "帮我比较一下以前的训练"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "时间范围" in response.json()["reply"]
    run = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run.json()["execution_mode"] == "clarify"
    assert run.json()["execution_trace"]["terminal_action"] == "clarify"
    invoke_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_affirmative_followup_routes_from_conversation_context(client):
    token = await _token(client, "agent-followup@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    async def contextual_model_outcome(message, *, context_messages=None, **_kwargs):
        if message == "需要":
            assert context_messages
            assert any(
                "需要我帮你查下次该练什么吗" in item["content"]
                for item in context_messages
            )
            return IntentResolverOutcome(
                resolution=normalize_resolution(
                    message,
                    IntentResolution(
                        primary_intent="next_workout_query",
                        intent_domain="workout_session",
                        request_kind="query",
                        requested_effect="read",
                        evidence_requirements=["next_workout"],
                        resolved_query="查询下一次训练安排",
                        confidence=0.96,
                    ),
                ),
                source="model",
                attempt_count=1,
            )
        return IntentResolverOutcome(
            resolution=normalize_resolution(
                message,
                IntentResolution(
                    primary_intent="general_qa",
                    intent_domain="general",
                    request_kind="query",
                    requested_effect="read",
                    resolved_query=message,
                    confidence=0.9,
                ),
            ),
            source="model",
            attempt_count=1,
        )
    invoke_agent = AsyncMock(side_effect=[
        {"messages": [AIMessage(content="需要我帮你查下次该练什么吗？")]},
        {"messages": [AIMessage(content="你的下一练已经查到了。")]},
    ])
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=contextual_model_outcome,
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=invoke_agent,
        ),
    ):
        first = await client.post(
            "/api/v1/agent/chat",
            json={"message": "我不知道接下来怎么安排"},
            headers=headers,
        )
        second = await client.post(
            "/api/v1/agent/chat",
            json={
                "message": "需要",
                "conversation_id": first.json()["conversation_id"],
            },
            headers=headers,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    second_run = await client.get(
        f"/api/v1/agent/runs/{second.json()['run_id']}",
        headers=headers,
    )
    assert second_run.json()["primary_intent"] == "next_workout_query"
    assert second_run.json()["tool_allowlist"] == ["workout.get_next"]
    assert second_run.json()["intent_source"] == "model"
    assert second_run.json()["intent_fallback_reason"] is None


@pytest.mark.asyncio
async def test_agent_persists_clarification_and_resumes_after_slot_fill(
    client,
    db_session,
):
    token = await _token(client, "agent-slot-fill@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    clarification = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="workout_history_query",
            resolved_query="比较我的训练历史",
            subtasks=["读取训练历史", "比较训练表现"],
            missing_slots=["要比较的时间范围"],
            clarification_required=True,
            clarification_question="你想比较哪个时间范围？",
            confidence=0.72,
        ),
        source="model",
        attempt_count=1,
    )
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=clarification),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(),
        ) as first_invoke,
    ):
        first = await client.post(
            "/api/v1/agent/chat",
            json={"message": "帮我比较训练表现"},
            headers=headers,
        )

    assert first.status_code == 200
    assert first.json()["reply"] == "你想比较哪个时间范围？"
    first_invoke.assert_not_awaited()
    conversation = await db_session.get(
        AgentConversation,
        first.json()["conversation_id"],
    )
    assert conversation is not None
    assert conversation.pending_clarification["missing_slots"] == [
        "要比较的时间范围"
    ]

    filled_resolution = IntentResolverOutcome(
        resolution=normalize_resolution(
            "最近四周",
            IntentResolution(
                primary_intent="workout_history_query",
                intent_domain="workout_session",
                request_kind="query",
                requested_effect="read",
                evidence_requirements=["workout_history"],
                resolved_query="比较我的训练历史；补充信息：最近四周",
                confidence=0.96,
            ),
        ),
        source="rules",
        fallback_reason="clarification_filled",
    )
    with (
        patch(
            "app.services.agent_runtime.resolve_intent_with_fallback",
            new=AsyncMock(return_value=filled_resolution),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(return_value={
                "messages": [AIMessage(content="这是你最近四周的训练比较。")],
            }),
        ) as second_invoke,
    ):
        second = await client.post(
            "/api/v1/agent/chat",
            json={
                "message": "最近四周",
                "conversation_id": first.json()["conversation_id"],
            },
            headers=headers,
        )

    assert second.status_code == 200
    second_run = await client.get(
        f"/api/v1/agent/runs/{second.json()['run_id']}",
        headers=headers,
    )
    run_body = second_run.json()
    assert run_body["intent_fallback_reason"] == "clarification_filled"
    assert run_body["tool_allowlist"] == ["workout.list_history"]
    assert run_body["missing_slots"] == []
    assert "最近四周" in run_body["resolved_query"]
    second_invoke.assert_awaited_once()
    await db_session.refresh(conversation)
    assert conversation.pending_clarification == {}
