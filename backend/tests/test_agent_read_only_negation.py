"""Regression for read requests rejected by the legacy mutation consistency check."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import AgentProposal, AgentRun, AgentToolCall
from app.models.exercise import Exercise
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan
from app.services.agent_intent import resolve_intent, route_tools
from app.services.agent_intent_model import (
    IntentRouteDecision,
    resolve_intent_with_fallback,
)
from app.services.agent_jobs import process_agent_run


READ_ONLY_MESSAGES = [
    "查看我当前的训练计划，只查看，不修改。",
    "查看我当前的训练计划，只查看，不修改",
    "查看我当前的训练计划，只查看，不 修改。",
    "查看我当前的训练计划，只查看不修改。",
    "不修改训练计划，只展示当前安排。",
    "查看当前训练计划，不删除。",
    "查看当前训练计划，不更新也不保存。",
    "查看当前训练计划，不调整、不删除。",
    "查看当前训练计划，不修改或删除。",
    "查看当前训练计划，不要修改。",
    "查看当前训练计划，不用修改。",
    "查看当前训练计划，先别修改。",
    "查看当前训练计划，无需修改。",
    "查看当前训练计划，不必修改。",
    "查看当前训练计划，暂不修改。",
]

MIXED_MESSAGES = [
    "不要修改周二的卧推，把周三的卧推休息改为180秒。",
    "把周三的卧推休息改为180秒，不要修改周二的卧推。",
    "周二的卧推不修改，把周三的卧推休息改为180秒。",
    "把周三的卧推休息改为180秒，周二的卧推不修改。",
    "周二的卧推不修改；把周三的卧推休息改为180秒。",
    "不删除周二的卧推\n把周三的卧推休息改为180秒。",
    "不是只查看，请把周三的卧推休息改为180秒。",
    "不得不修改当前训练计划",
    "不能不修改当前训练计划",
    "不是不修改当前训练计划",
]


def _read_route():
    return IntentRouteDecision(
        intent_domain="workout_plan", request_kind="query", requested_effect="read",
        requested_output="answer", read_targets=["active_plan"],
        decision_action=None, artifact_action=None,
        normalized_request="查看当前活动训练计划，不进行修改",
        risk_level="low", confidence=0.99,
    )


@pytest.mark.parametrize("message", READ_ONLY_MESSAGES)
def test_negated_write_verbs_do_not_invent_a_mutation(message):
    result = resolve_intent(message)
    assert result.request_kind == "query"
    assert result.requested_effect == "read"
    assert result.change_requests == []


@pytest.mark.parametrize("message", READ_ONLY_MESSAGES)
@pytest.mark.asyncio
async def test_valid_model_read_is_not_rejected_or_repaired(message):
    # Keep the real route conversion, normalizer and server consistency check.
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-read-negation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(return_value=_read_route())) as route,
        patch("app.services.agent_intent_model._invoke_model_change_extraction",
              new=AsyncMock()) as extraction,
    ):
        result = await resolve_intent_with_fallback(message, use_model=True)
    route.assert_awaited_once()
    extraction.assert_not_awaited()
    assert result.source == "model"
    assert result.error_category is None
    assert result.understanding_failed is False
    assert result.resolution.request_kind == "query"
    assert result.resolution.change_requests == []
    assert route_tools(result.resolution) == ["plan.get_active"]


@pytest.mark.parametrize("message", MIXED_MESSAGES)
@pytest.mark.asyncio
async def test_negation_does_not_cancel_another_explicit_mutation(message):
    assert resolve_intent(message).request_kind == "mutation"
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-read-negation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(return_value=_read_route())) as route,
    ):
        result = await resolve_intent_with_fallback(message, use_model=True)
    assert route.await_count == 2
    assert result.understanding_failed is True
    assert result.error_category == "semantic_mutation_structure_missing"
    assert route_tools(result.resolution) == []


@pytest.mark.parametrize("failure", ["disabled", "unconfigured", "timeout"])
@pytest.mark.asyncio
async def test_negated_read_does_not_bypass_unavailable_semantic_model(failure):
    with (
        patch.object(settings, "DEEPSEEK_API_KEY",
                     "" if failure == "unconfigured" else "test-read-negation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(side_effect=TimeoutError)) as route,
    ):
        result = await resolve_intent_with_fallback(
            READ_ONLY_MESSAGES[0], use_model=failure != "disabled",
        )
    assert result.understanding_failed is True
    assert route_tools(result.resolution) == []
    if failure != "timeout":
        route.assert_not_awaited()


@pytest.mark.parametrize(("message", "domain", "evidence"), [
    ("查看我已完成的训练记录，不修改。", "workout_history", "workout_history"),
    ("查看我的体重，不修改资料。", "profile", "profile_summary"),
    ("查看今日饮食记录，不删除。", "nutrition", "nutrition_today"),
    ("查看健康资料，不更新。", "health", "health_screening"),
])
@pytest.mark.asyncio
async def test_negation_fix_preserves_other_read_domains(message, domain, evidence):
    assert resolve_intent(message).request_kind == "query"
    candidate = _read_route().model_copy(update={
        "intent_domain": domain, "read_targets": [evidence], "normalized_request": message,
    })
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "test-read-negation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(return_value=candidate)) as route,
    ):
        result = await resolve_intent_with_fallback(message, use_model=True)
    route.assert_awaited_once()
    assert result.understanding_failed is False
    assert result.resolution.intent_domain == domain
    assert result.resolution.request_kind == "query"
    assert result.resolution.change_requests == []


class _ReadPlanModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **_kwargs):
        assert [tool.name for tool in tools] == ["plan_get_active"]
        return self


@pytest.mark.parametrize("mode", ["chat", "async_run"])
@pytest.mark.asyncio
async def test_negated_read_reaches_owned_plan_without_business_writes(
    client, db_session, session_factory, mode,
):
    registration = await client.post("/api/v1/auth/register", json={
        "email": f"read-negation-{mode}@example.com", "password": "test-password",
    })
    assert registration.status_code == 201
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}
    user = await db_session.scalar(select(User).where(
        User.email == f"read-negation-{mode}@example.com",
    ))
    plan = WorkoutPlan(user_id=user.id, name="只读验收计划", days_per_week=1)
    exercise = Exercise(
        owner_id=user.id,
        name_zh="只读验收卧推", name_en=f"Read Negation Bench {mode}",
        category="strength", difficulty="beginner", equipment=["barbell"],
    )
    db_session.add_all([plan, exercise])
    await db_session.flush()
    entry = PlannedExercise(
        plan_id=plan.id, exercise_id=exercise.id, day_of_week=3,
        sets=3, reps="10", rest_seconds=150, recommended_weight_kg=40,
    )
    db_session.add(entry)
    await db_session.commit()

    async def business_snapshot():
        plans = (await db_session.execute(select(*WorkoutPlan.__table__.c).where(
            WorkoutPlan.user_id == user.id,
        ))).mappings().all()
        entries = (await db_session.execute(select(*PlannedExercise.__table__.c).where(
            PlannedExercise.plan_id == plan.id,
        ))).mappings().all()
        proposals = await db_session.scalar(select(func.count(AgentProposal.id)).where(
            AgentProposal.user_id == user.id,
        ))
        return plans, entries, proposals

    before = await business_snapshot()
    model = _ReadPlanModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "plan_get_active", "args": {},
            "id": f"read-plan-{mode}", "type": "tool_call",
        }]),
        AIMessage(content="当前计划为只读验收计划，周三卧推休息150秒。没有修改。"),
    ])
    with (
        patch.object(settings, "AGENT_ENABLED", True),
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-read-negation-key"),
        patch("app.services.agent_intent_model._invoke_model_route",
              new=AsyncMock(return_value=_read_route())) as route,
        patch("app.services.agent_intent_model._invoke_model_change_extraction",
              new=AsyncMock()) as extraction,
        patch("app.services.agent_runtime._build_model", return_value=model),
    ):
        if mode == "chat":
            response = await client.post("/api/v1/agent/chat", headers=headers,
                                         json={"message": READ_ONLY_MESSAGES[0]})
            assert response.status_code == 200
        else:
            response = await client.post("/api/v1/agent/runs", headers=headers, json={
                "message": READ_ONLY_MESSAGES[0],
                "client_request_id": "read-negation-async-request",
            })
            assert response.status_code == 202
            # Supply a worker lease only for this fixture. Global queue claims
            # can select unrelated tests' queued runs in the full suite; the
            # queue-claim lifecycle itself is covered in test_agent_async_jobs.
            queued = await db_session.get(AgentRun, response.json()["run_id"])
            assert queued.user_id == user.id and queued.status == "queued"
            now = datetime.now(timezone.utc)
            queued.status = "running"
            queued.processing_started_at = now
            queued.attempt_count = 1
            queued.lease_expires_at = now + timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS)
            await db_session.commit()
            await process_agent_run(session_factory, queued.id)
        run_id = response.json()["run_id"]
        run_response = await client.get(f"/api/v1/agent/runs/{run_id}", headers=headers)

    body = run_response.json()
    assert body["status"] == "completed"
    assert body["intent_source"] == "model"
    assert body["intent_error_category"] is None
    assert body["tool_allowlist"] == ["plan.get_active"]
    assert "只读验收计划" in body["reply"]
    card = next(card for card in body["cards"] if card["type"] == "plan.get_active")
    assert card["data"]["plan"]["id"] == plan.id
    assert card["data"]["plan"]["exercises"][0]["rest_seconds"] == 150
    route.assert_awaited_once()
    extraction.assert_not_awaited()
    assert await business_snapshot() == before
    calls = (await db_session.scalars(select(AgentToolCall).where(
        AgentToolCall.run_id == run_id,
    ))).all()
    assert [call.tool_name for call in calls] == ["plan.get_active"]
