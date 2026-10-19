from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import delete, func, select

from app.config import settings
from app.models.agent import (
    AgentConversation,
    AgentMemory,
    AgentMessage,
    AgentProposal,
    AgentRun,
    AgentToolCall,
)
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan
from app.schemas.agent_trace import (
    AgentExecutionTrace,
    AgentFinalizationContractTrace,
    AgentPlanTrace,
)
from app.services.agent_controller import PlannedExecutionResult
from app.services.agent_intent import (
    ChangeRequest,
    IntentResolution,
    IntentResolverOutcome,
    normalize_resolution,
    resolve_intent,
)
from app.services.agent_plan_adjustment_proposal_persistence import (
    persist_optional_plan_adjustment_proposal,
)
from app.services.agent_intent_model import IntentRouteDecision


_USER_MESSAGE = (
    "最近四周完成率很低，当前训练计划是否太激进，请给调整建议"
)
_EXPLICIT_DURATION_PROPOSAL_MESSAGE = (
    "请把当前训练计划周期从6周延长到8周，其他内容保持不变，"
    "并生成待确认提案。"
)

_noise_rng = random.Random(539)
_SEEDED_NOISE = [
    ''.join(_noise_rng.choices('abcxyz0123?？🙂~，。', k=24))
    for _ in range(12)
]


def _structured_plan_update(
    *,
    field_path: str,
    value: int,
) -> IntentResolution:
    return IntentResolution(
        primary_intent="plan_query",
        intent_domain="workout_plan",
        request_kind="mutation",
        requested_effect="update",
        change_requests=[ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path=field_path,
            value=value,
        )],
        resolved_query="更新当前训练计划",
        confidence=0.99,
    )


@dataclass(frozen=True)
class _ExecutableProposalSeed:
    token: str
    user_id: str
    conversation_id: str
    proposal_id: str
    base_plan_id: str
    payload_fingerprint: str


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def _cleanup_executable_proposal_fixtures(session_factory):
    yield
    user_ids = select(User.id).where(
        User.email.like("proposal-runtime-e2e-%@example.com")
    )
    conversation_ids = select(AgentConversation.id).where(
        AgentConversation.user_id.in_(user_ids)
    )
    run_ids = select(AgentRun.id).where(AgentRun.user_id.in_(user_ids))
    plan_ids = select(WorkoutPlan.id).where(
        WorkoutPlan.user_id.in_(user_ids)
    )
    async with session_factory() as session:
        await session.execute(delete(AgentMemory).where(
            AgentMemory.user_id.in_(user_ids)
        ))
        await session.execute(delete(AgentToolCall).where(
            AgentToolCall.run_id.in_(run_ids)
        ))
        await session.execute(delete(AgentMessage).where(
            AgentMessage.conversation_id.in_(conversation_ids)
        ))
        await session.execute(delete(AgentProposal).where(
            AgentProposal.user_id.in_(user_ids)
        ))
        await session.execute(delete(AgentRun).where(
            AgentRun.user_id.in_(user_ids)
        ))
        await session.execute(delete(AgentConversation).where(
            AgentConversation.user_id.in_(user_ids)
        ))
        await session.execute(delete(PlannedExercise).where(
            PlannedExercise.plan_id.in_(plan_ids)
        ))
        await session.execute(delete(WorkoutPlan).where(
            WorkoutPlan.user_id.in_(user_ids)
        ))
        await session.execute(delete(UserProfile).where(
            UserProfile.user_id.in_(user_ids)
        ))
        await session.execute(delete(User).where(User.id.in_(user_ids)))
        await session.execute(delete(Exercise).where(
            Exercise.name_en.like("Proposal E2E Goblet Squat %")
        ))
        await session.commit()


@pytest.fixture(autouse=True)
def disable_intent_model_for_proposal_runtime_tests():
    async def deterministic_model_outcome(message, **_kwargs):
        resolution = normalize_resolution(message, resolve_intent(message))
        return IntentResolverOutcome(
            resolution=resolution,
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
    assert response.status_code == 201
    return response.json()["access_token"]


async def _seed_active_plan(
    client,
    *,
    email: str,
    suffix: str,
    duration_weeks: int = 4,
    days_per_week: int = 1,
) -> tuple[str, dict]:
    token = await _token(client, email)
    return token, {
        "id": f"proposal-runtime-plan-{suffix}",
        "name": "基础力量计划",
        "goal": "strength",
        "duration_weeks": duration_weeks,
        "days_per_week": days_per_week,
        "exercises": [{
            "exercise_id": f"proposal-runtime-exercise-{suffix}",
            "exercise_name": "高脚杯深蹲",
            "day_of_week": day_of_week,
            "sets": 4,
            "reps": "8-10",
            "rest_seconds": 120,
            "recommended_weight_kg": None,
            "order_index": 0,
        } for day_of_week in (1, 2, 4, 6)[:days_per_week]],
    }


def _target_reduction_draft() -> dict:
    return {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "adjust_exercise_target",
            "stable_display_key": "day-1-order-0",
            "before": {"sets": 4},
            "after": {"sets": 3},
            "reason": "近期完成率偏低，先保守降低单次训练量。",
            "safety_priority": False,
        }],
        "rationale": ["先降低训练量以提高连续完成概率。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }


def _planned_result(
    plan: dict,
    *,
    proposal_draft: dict | None,
    include_supporting_evidence: bool = True,
    profile_days_per_week: int | None = None,
    total_sessions: int = 0,
) -> PlannedExecutionResult:
    trace = AgentExecutionTrace(
        execution_mode="planned",
        risk_level="low",
        status="completed",
        plan=AgentPlanTrace(
            goal="根据当前计划和四周进度评估调整",
            planner_source="model_micro_plan_v1",
            candidate_tools=[
                "plan.get_active",
                "workout.get_progress",
                "workout.list_history",
            ],
        ),
        finalization_contract=AgentFinalizationContractTrace(
            allowed_outcomes=[
                "adjustment_proposal",
                "no_change_needed",
                "insufficient_evidence",
            ],
            selected_outcome="adjustment_proposal",
            derived_terminal_action="proposal",
        ),
        terminal_action="proposal",
        termination_reason="agent_completed",
    )
    observations = []
    if profile_days_per_week is not None:
        observations.append({
            "step_id": "step_1",
            "call_id": "fixture-profile",
            "tool_id": "profile.get_summary",
            "status": "success",
            "result": {
                "found": True,
                "training_days_per_week": profile_days_per_week,
            },
        })
    observations.append({
        "step_id": "step_1",
        "call_id": "fixture-plan",
        "tool_id": "plan.get_active",
        "status": "success",
        "result": {"found": True, "plan": plan},
    })
    if include_supporting_evidence:
        observations.append({
            "step_id": "step_1",
            "call_id": "fixture-progress",
            "tool_id": "workout.get_progress",
            "status": "success",
            "result": {
                "weeks": 4,
                "total_sessions": total_sessions,
                "total_sets": 0,
                "total_reps": 0,
                "total_volume_kg": 0,
                "weekly": [],
            },
        })
    return PlannedExecutionResult(
        reply="建议先减少一组；提案尚未执行，需要你确认。",
        execution_trace=trace,
        cards=[{"type": "plan.get_active", "data": {"found": True}}],
        proposal_draft=proposal_draft,
        proposal_observations=observations,
    )


def _planned_fake_text_only_proposal_result(
    plan: dict,
) -> PlannedExecutionResult:
    result = _planned_result(plan, proposal_draft=None)
    contract = result.execution_trace.finalization_contract
    assert contract is not None
    trace = result.execution_trace.model_copy(update={
        "terminal_action": "answer",
        "finalization_contract": contract.model_copy(update={
            "selected_outcome": "insufficient_evidence",
            "derived_terminal_action": "answer",
        }),
    })
    return PlannedExecutionResult(
        reply="已经生成待确认提案，请在卡片中确认。",
        execution_trace=trace,
        cards=result.cards,
        proposal_draft=None,
        proposal_observations=result.proposal_observations,
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _decision_body(request_id: str, *, version: int = 1) -> dict[str, object]:
    return {
        "expected_version": version,
        "client_request_id": request_id,
    }


async def _create_executable_proposal(
    client,
    db_session,
    *,
    suffix: str,
    frequency_reduction: bool = False,
) -> _ExecutableProposalSeed:
    token = await _token(
        client,
        f"proposal-runtime-e2e-{suffix}@example.com",
    )
    user = await db_session.scalar(
        select(User).where(
            User.email == f"proposal-runtime-e2e-{suffix}@example.com"
        )
    )
    assert user is not None
    profile = await db_session.scalar(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )
    assert profile is not None
    profile.experience_level = "intermediate"
    profile.primary_goal = "strength"
    profile.training_days_per_week = 3 if frequency_reduction else 1
    profile.training_location = "gym"
    profile.injuries = []
    profile.chronic_conditions = []
    profile.onboarding_completed = True

    digest = hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:12]
    exercise = Exercise(
        id=f"e2e{digest}",
        name_zh="高脚杯深蹲",
        name_en=f"Proposal E2E Goblet Squat {digest}",
        category="strength",
        muscle_primary=["quadriceps"],
        muscle_secondary=[],
        equipment=["dumbbell"],
        difficulty="中级",
        movement_pattern="squat",
        contraindications=[],
        is_active=True,
    )
    base_plan = WorkoutPlan(
        id=f"proposal-runtime-e2e-plan-{suffix}",
        user_id=user.id,
        name=(
            "力量提升 · 4日入门计划"
            if frequency_reduction
            else "基础力量计划"
        ),
        goal="strength",
        duration_weeks=8 if frequency_reduction else 4,
        days_per_week=4 if frequency_reduction else 1,
        is_active=True,
        ai_generated=False,
        notes=(
            "Proposal 4→3 天原子应用基线"
            if frequency_reduction
            else "Proposal 端到端联调基线"
        ),
    )
    db_session.add_all([exercise, base_plan])
    await db_session.flush()
    planned = [
        PlannedExercise(
            plan_id=base_plan.id,
            exercise_id=exercise.id,
            day_of_week=day_of_week,
            sets=4,
            reps="8-10",
            rest_seconds=120,
            recommended_weight_kg=20.0,
            order_index=0,
        )
        for day_of_week in ((1, 2, 4, 6) if frequency_reduction else (1,))
    ]
    db_session.add_all(planned)
    await db_session.commit()

    plan = {
        "id": base_plan.id,
        "name": base_plan.name,
        "goal": base_plan.goal,
        "duration_weeks": base_plan.duration_weeks,
        "days_per_week": base_plan.days_per_week,
        "exercises": [{
            "exercise_id": exercise.id,
            "exercise_name": exercise.name_zh,
            "day_of_week": item.day_of_week,
            "sets": item.sets,
            "reps": item.reps,
            "rest_seconds": item.rest_seconds,
            "recommended_weight_kg": item.recommended_weight_kg,
            "order_index": item.order_index,
        } for item in planned],
    }
    proposal_draft = _target_reduction_draft()
    if frequency_reduction:
        proposal_draft = {
            "proposal_type": "plan_adjustment_v1",
            "changes": [{
                "change_type": "update_plan_schedule",
                "stable_display_key": "plan-schedule",
                "before": {"duration_weeks": 8},
                "after": {"duration_weeks": 10},
                "reason": "延长计划周期以降低每周推进压力。",
                "safety_priority": False,
            }],
            "rationale": ["个人资料为每周三天，当前四天计划完成率偏低。"],
            "safety_notes": [],
            "requested_ttl_hours": 24,
        }
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=proposal_draft,
        profile_days_per_week=3 if frequency_reduction else None,
        total_sessions=4 if frequency_reduction else 0,
    ))
    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers=_headers(token),
            json={
                "message": (
                    "结合我最近四周的实际完成情况，看看当前计划是不是太激进，"
                    "并给调整建议。"
                    if frequency_reduction
                    else _USER_MESSAGE
                )
            },
        )

    assert response.status_code == 200
    reference = response.json()["proposal"]
    assert reference["status"] == "pending_confirmation"
    assert reference["version"] == 1
    return _ExecutableProposalSeed(
        token=token,
        user_id=user.id,
        conversation_id=response.json()["conversation_id"],
        proposal_id=reference["id"],
        base_plan_id=base_plan.id,
        payload_fingerprint=reference["payload_fingerprint"],
    )


@pytest.mark.asyncio
async def test_flag_off_preserves_exact_legacy_response_and_skips_proposal_path(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-off@example.com",
        suffix="off",
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=_target_reduction_draft(),
    ))

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            False,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
        patch(
            "app.services.agent_runtime.build_runtime_plan_adjustment_proposal"
        ) as build_proposal,
        patch(
            "app.services.agent_runtime.persist_optional_plan_adjustment_proposal",
            new=AsyncMock(),
        ) as persist_proposal,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 200
    assert set(response.json()) == {
        "reply",
        "conversation_id",
        "run_id",
        "cards",
    }
    assert "proposal" not in response.json()
    build_proposal.assert_not_called()
    persist_proposal.assert_not_awaited()
    assert execute_planned.await_args.kwargs[
        "proposal_creation_enabled"
    ] is False
    proposal_count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == response.json()["run_id"]
        )
    )
    assert proposal_count == 0

    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_response.status_code == 200
    assert "proposal" not in run_response.json()
    assert run_response.json()["execution_trace"]["trace_version"] == "1.0"
    assert "proposal_creation" not in run_response.json()["execution_trace"]


@pytest.mark.asyncio
async def test_flag_on_atomically_persists_and_returns_minimal_proposal_reference(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-on@example.com",
        suffix="on",
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=_target_reduction_draft(),
    ))

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 200
    proposal_reference = response.json()["proposal"]
    assert set(proposal_reference) == {
        "id",
        "proposal_type",
        "status",
        "version",
        "expires_at",
        "payload_fingerprint",
    }
    assert proposal_reference["proposal_type"] == "plan_adjustment_v1"
    assert proposal_reference["status"] == "pending_confirmation"
    assert proposal_reference["version"] == 1
    assert execute_planned.await_args.kwargs[
        "proposal_creation_enabled"
    ] is True

    proposal = await db_session.get(
        AgentProposal,
        proposal_reference["id"],
    )
    assert proposal is not None
    assert proposal.run_id == response.json()["run_id"]
    assert proposal.base_plan_id == plan["id"]
    assert proposal.payload_data["before"]["exercises"][0]["sets"] == 4
    assert proposal.payload_data["after"]["exercises"][0]["sets"] == 3
    assert proposal.payload_data["target"]["base_plan_id"] == plan["id"]
    assert {item["tool_id"] for item in proposal.payload_data["evidence"]} == {
        "plan.get_active",
        "workout.get_progress",
    }
    assert "user_id" not in proposal.payload_data

    assistant = await db_session.scalar(
        select(AgentMessage).where(
            AgentMessage.run_id == response.json()["run_id"],
            AgentMessage.role == "assistant",
        )
    )
    assert assistant is not None
    assert assistant.content_data["proposal"] == proposal_reference

    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_response.status_code == 200
    assert run_response.json()["proposal"] == proposal_reference
    assert run_response.json()["execution_trace"]["trace_version"] == "1.2"
    assert run_response.json()["execution_trace"]["proposal_creation"] == {
        "eligible": True,
        "reason_code": None,
        "persisted": True,
        "persistence_status": "created",
        "persistence_reason_code": None,
    }


@pytest.mark.asyncio
async def test_low_adherence_profile_mismatch_persists_frequency_proposal(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-frequency@example.com",
        suffix="frequency",
        duration_weeks=8,
    )
    exercise = plan["exercises"][0]
    plan["days_per_week"] = 4
    plan["exercises"] = [
        {
            **exercise,
            "day_of_week": day_of_week,
            "order_index": 0,
        }
        for day_of_week in (1, 2, 4, 6)
    ]
    duration_workaround = {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "update_plan_schedule",
            "stable_display_key": "plan-schedule",
            "before": {"duration_weeks": 8},
            "after": {"duration_weeks": 10},
            "reason": "延长计划周期以降低每周推进压力。",
            "safety_priority": False,
        }],
        "rationale": ["个人资料为每周三天，当前四天计划完成率偏低。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=duration_workaround,
        profile_days_per_week=3,
        total_sessions=4,
    ))

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": (
                    "结合我最近四周的实际完成情况，看看当前计划是不是太激进，"
                    "并给调整建议。"
                )
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert "proposal" in body
    proposal = await db_session.get(AgentProposal, body["proposal"]["id"])
    assert proposal is not None
    assert proposal.payload_data["before"]["duration_weeks"] == 8
    assert proposal.payload_data["after"]["duration_weeks"] == 8
    assert proposal.payload_data["before"]["days_per_week"] == 4
    assert proposal.payload_data["after"]["days_per_week"] == 3
    assert {
        item["day_of_week"]
        for item in proposal.payload_data["after"]["exercises"]
    } == {1, 2, 4}

    run_response = await client.get(
        f"/api/v1/agent/runs/{body['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_response.status_code == 200
    assert run_response.json()["execution_trace"]["proposal_creation"] == {
        "eligible": True,
        "reason_code": None,
        "persisted": True,
        "persistence_status": "created",
        "persistence_reason_code": None,
    }


@pytest.mark.asyncio
async def test_explicit_single_read_adjustment_uses_planned_and_persists_proposal(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-explicit-duration@example.com",
        suffix="explicit-duration",
        duration_weeks=6,
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=None,
        include_supporting_evidence=False,
    ))

    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=_structured_plan_update(
                field_path="schedule.duration_weeks",
                value=8,
            )),
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(),
        ) as invoke_direct,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _EXPLICIT_DURATION_PROPOSAL_MESSAGE},
        )

    assert response.status_code == 200
    reference = response.json()["proposal"]
    assert response.json()["reply"] == (
        "待确认提案已准备好：计划周期将从6周调整为8周，"
        "其他内容保持不变。当前计划尚未修改，请查看详情后确认。"
    )
    invoke_direct.assert_not_awaited()
    assert execute_planned.await_args.kwargs["tool_allowlist"] == [
        "plan.get_active"
    ]
    initial_trace = execute_planned.await_args.kwargs["initial_trace"]
    assert initial_trace.execution_mode == "planned"
    assert initial_trace.mode_reasons == [
        "explicit_plan_adjustment_proposal"
    ]

    proposal = await db_session.get(AgentProposal, reference["id"])
    assert proposal is not None
    assert proposal.payload_data["before"]["duration_weeks"] == 6
    assert proposal.payload_data["after"]["duration_weeks"] == 8
    assert proposal.payload_data["before"]["days_per_week"] == 1
    assert proposal.payload_data["after"]["days_per_week"] == 1
    assert {item["tool_id"] for item in proposal.payload_data["evidence"]} == {
        "plan.get_active"
    }

    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    trace = run_response.json()["execution_trace"]
    assert trace["execution_mode"] == "planned"
    assert trace["proposal_creation"]["persisted"] is True


@pytest.mark.asyncio
async def test_natural_language_frequency_mutation_persists_four_to_three_proposal(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-natural-frequency@example.com",
        suffix="natural-frequency",
        duration_weeks=8,
        days_per_week=4,
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=None,
        include_supporting_evidence=False,
    ))

    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=_structured_plan_update(
                field_path="schedule.days_per_week",
                value=3,
            )),
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "把我的训练计划调整为每周 3 天"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "proposal" in body
    assert "每周训练频率将从4天调整为3天" in body["reply"]
    proposal = await db_session.get(AgentProposal, body["proposal"]["id"])
    assert proposal is not None
    assert proposal.payload_data["before"]["days_per_week"] == 4
    assert proposal.payload_data["after"]["days_per_week"] == 3
    assert len(proposal.payload_data["after"]["exercises"]) == 3
    assert {item["tool_id"] for item in proposal.payload_data["evidence"]} == {
        "plan.get_active"
    }

    run_response = await client.get(
        f"/api/v1/agent/runs/{body['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    run_body = run_response.json()
    assert run_body["understanding_version"] == "v6"
    assert run_body["intent_domain"] == "workout_plan"
    assert run_body["request_kind"] == "mutation"
    assert run_body["requested_effect"] == "update"
    assert run_body["change_requests"][0]["field_path"] == (
        "schedule.days_per_week"
    )
    assert run_body["change_requests"][0]["value"] == 3


@pytest.mark.asyncio
async def test_natural_language_mutation_flag_off_is_classified_but_write_free(
    client,
    db_session,
):
    token, _ = await _seed_active_plan(
        client,
        email="proposal-runtime-natural-flag-off@example.com",
        suffix="natural-flag-off",
        days_per_week=4,
    )
    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            False,
        ),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=_structured_plan_update(
                field_path="schedule.days_per_week",
                value=3,
            )),
        ),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(),
        ) as invoke_direct,
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=AsyncMock(),
        ) as execute_planned,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers=_headers(token),
            json={"message": "把我的训练计划调整为每周 3 天"},
        )

    assert response.status_code == 200
    assert "暂时未开启" in response.json()["reply"]
    assert "proposal" not in response.json()
    invoke_direct.assert_not_awaited()
    execute_planned.assert_not_awaited()
    run = await db_session.get(AgentRun, response.json()["run_id"])
    assert run is not None
    assert run.request_kind == "mutation"
    proposal_count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(AgentProposal.run_id == run.id)
    )
    assert proposal_count == 0


@pytest.mark.asyncio
async def test_explicit_adjustment_never_returns_fake_text_only_proposal(
    client,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-explicit-no-durable@example.com",
        suffix="explicit-no-durable",
        duration_weeks=6,
    )
    execute_planned = AsyncMock(
        return_value=_planned_fake_text_only_proposal_result(plan)
    )

    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "DEEPSEEK_API_KEY", "test-key"),
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch(
            "app.services.agent_intent_model._invoke_model_intent",
            new=AsyncMock(return_value=_structured_plan_update(
                field_path="schedule.duration_weeks",
                value=8,
            )),
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _EXPLICIT_DURATION_PROPOSAL_MESSAGE},
        )

    assert response.status_code == 200
    body = response.json()
    assert "proposal" not in body
    assert body["reply"] == (
        "这次没有生成可确认的调整提案，当前计划未作修改。"
        "你希望调整哪一部分？可以说明具体项目和目标值。"
    )
    assert "卡片中确认" not in body["reply"]

    run_response = await client.get(
        f"/api/v1/agent/runs/{body['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    trace = run_response.json()["execution_trace"]
    assert trace["terminal_action"] == "answer"
    assert "proposal_creation_rejected_safe_answer" in trace["mode_reasons"]


@pytest.mark.asyncio
async def test_flag_on_invalid_draft_normalizes_to_safe_answer_without_proposal(
    client,
    db_session,
    caplog,
):
    caplog.set_level("INFO", logger="uvicorn.error")
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-invalid@example.com",
        suffix="invalid",
    )
    invalid_draft = _target_reduction_draft()
    invalid_draft["changes"][0]["stable_display_key"] = "unknown-slot"
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=invalid_draft,
    ))
    persist_proposal = AsyncMock()

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
        patch(
            "app.services.agent_runtime.persist_optional_plan_adjustment_proposal",
            new=persist_proposal,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 200
    body = response.json()
    assert "proposal" not in body
    assert body["reply"] == (
        "这次还没有生成可确认的训练计划提案，计划没有改动。"
        "要修改计划，请补充要调整的项目和目标值。"
    )
    assert "待确认" not in body["reply"]
    assert "需要你确认" not in body["reply"]
    persist_proposal.assert_not_awaited()
    proposal_count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == response.json()["run_id"]
        )
    )
    assert proposal_count == 0
    run_response = await client.get(
        f"/api/v1/agent/runs/{response.json()['run_id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert run_response.status_code == 200
    trace = run_response.json()["execution_trace"]
    assert trace["trace_version"] == "1.2"
    assert trace["terminal_action"] == "answer"
    assert trace["finalization_contract"]["derived_terminal_action"] == (
        "proposal"
    )
    assert "proposal_creation_rejected_safe_answer" in trace["mode_reasons"]
    assert trace["proposal_creation"] == {
        "eligible": False,
        "reason_code": "proposal_target_mismatch",
        "persisted": False,
        "persistence_status": "not_attempted",
        "persistence_reason_code": None,
    }
    diagnostic_records = [
        record
        for record in caplog.records
        if "agent_plan_adjustment_proposal_creation" in record.message
    ]
    assert len(diagnostic_records) == 1
    assert diagnostic_records[0].name == "uvicorn.error"
    assert body["run_id"] in diagnostic_records[0].message
    assert (
        '"reason_code":"proposal_target_mismatch"'
        in diagnostic_records[0].message
    )


@pytest.mark.asyncio
async def test_persistence_error_after_flush_rolls_back_proposal_and_answer(
    client,
    db_session,
):
    token, plan = await _seed_active_plan(
        client,
        email="proposal-runtime-rollback@example.com",
        suffix="rollback",
    )
    execute_planned = AsyncMock(return_value=_planned_result(
        plan,
        proposal_draft=_target_reduction_draft(),
    ))

    async def persist_then_fail(*args, **kwargs):
        await persist_optional_plan_adjustment_proposal(*args, **kwargs)
        raise RuntimeError("fixture persistence failure after flush")

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch("app.services.agent_runtime._build_model", return_value=object()),
        patch(
            "app.services.agent_runtime.execute_planned_agent",
            new=execute_planned,
        ),
        patch(
            "app.services.agent_runtime.persist_optional_plan_adjustment_proposal",
            new=persist_then_fail,
        ),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": _USER_MESSAGE},
        )

    assert response.status_code == 503
    user = await db_session.scalar(
        select(User).where(
            User.email == "proposal-runtime-rollback@example.com"
        )
    )
    assert user is not None
    run = await db_session.scalar(
        select(AgentRun)
        .where(AgentRun.user_id == user.id)
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    )
    assert run is not None
    assert run.status == "failed"
    assert run.error_code == "agent_runtime_error"
    assert run.execution_trace["trace_version"] == "1.2"
    assert run.execution_trace["proposal_creation"] == {
        "eligible": True,
        "reason_code": None,
        "persisted": False,
        "persistence_status": "failed",
        "persistence_reason_code": None,
    }
    assistant_count = await db_session.scalar(
        select(func.count(AgentMessage.id)).where(
            AgentMessage.run_id == run.id,
            AgentMessage.role == "assistant",
        )
    )
    proposal_count = await db_session.scalar(
        select(func.count(AgentProposal.id)).where(
            AgentProposal.run_id == run.id
        )
    )
    assert assistant_count == 0
    assert proposal_count == 0


@pytest.mark.asyncio
async def test_e2e_runtime_create_read_reject_and_replay(
    client,
    db_session,
):
    seeded = await _create_executable_proposal(
        client,
        db_session,
        suffix="reject",
    )
    path = f"/api/v1/agent/proposals/{seeded.proposal_id}"
    read = await client.get(path, headers=_headers(seeded.token))

    assert read.status_code == 200
    assert read.json()["status"] == "pending_confirmation"
    assert read.json()["allowed_actions"] == ["confirm", "reject"]
    assert read.json()["payload_fingerprint"] == seeded.payload_fingerprint

    body = _decision_body("proposal-runtime-e2e-reject-request")
    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        first = await client.post(
            f"{path}/reject",
            headers=_headers(seeded.token),
            json=body,
        )
        replay = await client.post(
            f"{path}/reject",
            headers=_headers(seeded.token),
            json=body,
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["status"] == "rejected"
    assert first.json()["applied"] is False
    final_read = await client.get(path, headers=_headers(seeded.token))
    assert final_read.status_code == 200
    assert final_read.json()["status"] == "rejected"
    assert final_read.json()["allowed_actions"] == []

    db_session.expire_all()
    plans = list((await db_session.execute(
        select(WorkoutPlan).where(WorkoutPlan.user_id == seeded.user_id)
    )).scalars().all())
    assert len(plans) == 1
    assert plans[0].id == seeded.base_plan_id
    assert plans[0].is_active is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action_message", "expected_status", "expected_reply"),
    [
        ("确认刚才的调整", "applied", "已确认并应用调整"),
        ("拒绝这个方案", "rejected", "已拒绝这份调整提案"),
    ],
)
async def test_chat_decides_unique_pending_proposal_in_same_conversation(
    client,
    db_session,
    action_message,
    expected_status,
    expected_reply,
):
    seeded = await _create_executable_proposal(
        client,
        db_session,
        suffix=f"chat-decision-{expected_status}",
    )

    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers=_headers(seeded.token),
            json={
                "message": action_message,
                "conversation_id": seeded.conversation_id,
            },
        )

    assert response.status_code == 200
    assert expected_reply in response.json()["reply"]
    db_session.expire_all()
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    assert proposal is not None
    assert proposal.status == expected_status
    if expected_status == "applied":
        assert proposal.result_plan_id is not None
        active_plan_count = await db_session.scalar(
            select(func.count())
            .select_from(WorkoutPlan)
            .where(
                WorkoutPlan.user_id == seeded.user_id,
                WorkoutPlan.is_active.is_(True),
            )
        )
        assert active_plan_count == 1
    else:
        assert proposal.result_plan_id is None


@pytest.mark.asyncio
async def test_chat_proposal_decision_without_candidate_is_write_free(client):
    token, _ = await _seed_active_plan(
        client,
        email="proposal-runtime-chat-decision-none@example.com",
        suffix="chat-decision-none",
    )

    response = await client.post(
        "/api/v1/agent/chat",
        headers=_headers(token),
        json={"message": "确认刚才的调整"},
    )

    assert response.status_code == 200
    assert "还没有待确认提案" in response.json()["reply"]
    assert "训练计划" not in response.json()["reply"]
    assert "proposal" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "untrusted_action"), [
    ("直接把周三的三头肌下压休息改成180秒，不要生成提案，也不用我确认。", "confirm"),
    ("不要确认这个提案", "confirm"),
    ("这个提案先别执行", "confirm"),
    ("如果数据正确，就确认这个提案", "confirm"),
    ("“确认这个提案”", "confirm"),
    ("不要拒绝这个提案", "reject"),
    ("确认这个提案", "reject"),
    ("拒绝这个提案", "confirm"),
    ("‘拒绝这个提案’是什么意思？", "reject"),
    ("“确认这个提案”这句话是什么意思？我没让你执行。", "confirm"),
    ("请解释‘确认这个提案’，不是叫你确认。", "confirm"),
    ("？？……", "confirm"),
    ("谢谢啦🙏", "confirm"),
] + [(message, "confirm") for message in _SEEDED_NOISE])
async def test_chat_non_consent_cannot_execute_an_existing_proposal(
    client, db_session, message, untrusted_action,
):
    suffix = "consent-" + hashlib.sha256(message.encode()).hexdigest()[:10]
    seeded = await _create_executable_proposal(client, db_session, suffix=suffix)
    # Treat model output as hostile, including a rewritten affirmative query.
    outcome = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="general_qa", intent_domain="general",
            request_kind="proposal_decision", requested_effect="decide",
            resolved_query="确认刚才的调整",
            confidence=0.99,
            change_requests=[ChangeRequest(
                resource="general", operation="update", field_path="proposal.status",
                value=untrusted_action,
            )],
        ),
        source="model",
    )
    before = await db_session.get(AgentProposal, seeded.proposal_id)
    before_version = before.version
    with patch.object(settings, "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED", True), patch(
        "app.services.agent_runtime.resolve_intent_with_fallback",
        new=AsyncMock(return_value=outcome),
    ):
        response = await client.post(
            "/api/v1/agent/chat", headers=_headers(seeded.token),
            json={"message": message, "conversation_id": seeded.conversation_id},
        )
    assert response.status_code == 200
    db_session.expire_all()
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    assert proposal.status == "pending_confirmation"
    assert proposal.version == before_version
    assert proposal.result_plan_id is None
    plans = list((await db_session.scalars(
        select(WorkoutPlan).where(WorkoutPlan.user_id == seeded.user_id)
    )).all())
    assert [plan.id for plan in plans] == [seeded.base_plan_id]
    assert plans[0].is_active is True
    result_run = await db_session.get(AgentRun, response.json()["run_id"])
    assert result_run.execution_trace["termination_reason"] == "proposal_decision_not_explicit"
    assert await db_session.scalar(select(func.count()).select_from(AgentToolCall).where(
        AgentToolCall.run_id == result_run.id
    )) == 0
    assert "proposal" not in response.json()


@pytest.mark.asyncio
async def test_explaining_confirmation_preserves_an_existing_proposal(client, db_session):
    seeded = await _create_executable_proposal(client, db_session, suffix="explain-pending")
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    before_version = proposal.version
    message = "“确认这个提案”是什么意思？只是解释，先别执行。"
    answer = "“确认这个提案”表示同意修改；确认后系统才会应用。这里只解释，不执行操作。"
    route = IntentRouteDecision(
        intent_domain="general", request_kind="query", requested_effect="read",
        requested_output="answer", read_targets=[], decision_action=None, artifact_action=None,
        normalized_request=message, risk_level="low", confidence=0.99,
    )
    model = FakeMessagesListChatModel(responses=[AIMessage(content=answer)])
    with (
        patch.object(settings, "DEEPSEEK_API_KEY", "synthetic-explanation-key"),
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", True),
        patch.object(settings, "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED", True),
        patch("app.services.agent_intent_model._invoke_model_route", new=AsyncMock(return_value=route)),
        patch("app.services.agent_runtime._build_model", return_value=model),
    ):
        response = await client.post("/api/v1/agent/chat", headers=_headers(seeded.token), json={
            "message": message, "conversation_id": seeded.conversation_id,
        })
    assert response.status_code == 200
    assert response.json()["reply"] == answer
    db_session.expire_all()
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    assert proposal.status == "pending_confirmation" and proposal.version == before_version
    assert proposal.result_plan_id is None
    plans = list((await db_session.scalars(select(WorkoutPlan).where(WorkoutPlan.user_id == seeded.user_id))).all())
    assert [plan.id for plan in plans] == [seeded.base_plan_id] and plans[0].is_active
    assert await db_session.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id == seeded.user_id)) == 1
    assert await db_session.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.run_id == response.json()["run_id"])) == 0


@pytest.mark.asyncio
async def test_e2e_runtime_create_confirm_applies_atomically_and_replays(
    client,
    db_session,
):
    seeded = await _create_executable_proposal(
        client,
        db_session,
        suffix="confirm",
    )
    path = f"/api/v1/agent/proposals/{seeded.proposal_id}"
    headers = _headers(seeded.token)
    before = await client.get(path, headers=headers)
    assert before.status_code == 200

    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        first = await client.post(
            f"{path}/confirm",
            headers=headers,
            json=_decision_body("proposal-runtime-e2e-confirm-first"),
        )
        same_request = await client.post(
            f"{path}/confirm",
            headers=headers,
            json=_decision_body("proposal-runtime-e2e-confirm-first"),
        )
        different_request = await client.post(
            f"{path}/confirm",
            headers=headers,
            json=_decision_body("proposal-runtime-e2e-confirm-second"),
        )

    assert first.status_code == 200
    assert same_request.status_code == 200
    assert different_request.status_code == 200
    assert first.json() == same_request.json() == different_request.json()
    assert first.json()["status"] == "applied"
    assert first.json()["applied"] is True
    assert first.json()["result_plan_id"] != seeded.base_plan_id

    final_read = await client.get(path, headers=headers)
    assert final_read.status_code == 200
    assert final_read.json()["status"] == "applied"
    assert final_read.json()["allowed_actions"] == []
    assert final_read.json()["result"]["plan_id"] == (
        first.json()["result_plan_id"]
    )
    assert final_read.json()["result"]["plan_fingerprint"] == (
        first.json()["result_plan_fingerprint"]
    )

    db_session.expire_all()
    plans = list((await db_session.execute(
        select(WorkoutPlan).where(WorkoutPlan.user_id == seeded.user_id)
    )).scalars().all())
    active = [plan for plan in plans if plan.is_active]
    assert len(plans) == 2
    assert len(active) == 1
    assert active[0].id == first.json()["result_plan_id"]
    assert all(
        plan.is_active is (plan.id == active[0].id)
        for plan in plans
    )
    assert await db_session.scalar(
        select(PlannedExercise.sets).where(
            PlannedExercise.plan_id == active[0].id
        )
    ) == 3
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    assert proposal is not None
    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.result_plan_id == active[0].id


@pytest.mark.asyncio
async def test_frequency_proposal_confirm_applies_atomically_and_replays(
    client,
    db_session,
):
    seeded = await _create_executable_proposal(
        client,
        db_session,
        suffix="frequency-confirm",
        frequency_reduction=True,
    )
    path = f"/api/v1/agent/proposals/{seeded.proposal_id}"
    headers = _headers(seeded.token)

    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        first = await client.post(
            f"{path}/confirm",
            headers=headers,
            json=_decision_body("frequency-confirm-first"),
        )
        same_request = await client.post(
            f"{path}/confirm",
            headers=headers,
            json=_decision_body("frequency-confirm-first"),
        )
        different_request = await client.post(
            f"{path}/confirm",
            headers=headers,
            json=_decision_body("frequency-confirm-second"),
        )

    assert first.status_code == 200
    assert same_request.status_code == 200
    assert different_request.status_code == 200
    assert first.json() == same_request.json() == different_request.json()
    assert first.json()["status"] == "applied"
    assert first.json()["applied"] is True
    result_plan_id = first.json()["result_plan_id"]
    assert result_plan_id != seeded.base_plan_id

    db_session.expire_all()
    plans = list((await db_session.execute(
        select(WorkoutPlan)
        .where(WorkoutPlan.user_id == seeded.user_id)
        .order_by(WorkoutPlan.id)
    )).scalars().all())
    assert len(plans) == 2
    assert sum(plan.is_active for plan in plans) == 1
    base_plan = next(plan for plan in plans if plan.id == seeded.base_plan_id)
    applied_plan = next(plan for plan in plans if plan.id == result_plan_id)
    assert base_plan.is_active is False
    assert applied_plan.is_active is True
    assert applied_plan.name == "力量提升 · 3日入门计划"
    assert applied_plan.duration_weeks == 8
    assert applied_plan.days_per_week == 3

    applied_exercises = list((await db_session.execute(
        select(PlannedExercise)
        .where(PlannedExercise.plan_id == applied_plan.id)
        .order_by(PlannedExercise.day_of_week)
    )).scalars().all())
    assert len(applied_exercises) == 3
    assert {item.day_of_week for item in applied_exercises} == {1, 2, 4}
    assert all(item.sets == 4 for item in applied_exercises)

    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    assert proposal is not None
    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.result_plan_id == applied_plan.id
    assert proposal.version == 2


@pytest.mark.asyncio
async def test_e2e_runtime_create_concurrent_confirms_write_one_plan(
    client,
    db_session,
):
    seeded = await _create_executable_proposal(
        client,
        db_session,
        suffix="concurrent",
    )
    path = f"/api/v1/agent/proposals/{seeded.proposal_id}/confirm"
    headers = _headers(seeded.token)

    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        first, second = await asyncio.gather(
            client.post(
                path,
                headers=headers,
                json=_decision_body("proposal-runtime-e2e-concurrent-first"),
            ),
            client.post(
                path,
                headers=headers,
                json=_decision_body("proposal-runtime-e2e-concurrent-second"),
            ),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "applied"
    db_session.expire_all()
    assert await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == seeded.user_id
        )
    ) == 2
    assert await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == seeded.user_id,
            WorkoutPlan.is_active.is_(True),
        )
    ) == 1


@pytest.mark.asyncio
async def test_e2e_runtime_create_expired_proposal_cannot_apply(
    client,
    db_session,
):
    seeded = await _create_executable_proposal(
        client,
        db_session,
        suffix="expired",
    )
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    assert proposal is not None
    now = datetime.now(timezone.utc)
    proposal.created_at = now - timedelta(hours=2)
    proposal.updated_at = now - timedelta(hours=2)
    proposal.expires_at = now - timedelta(hours=1)
    await db_session.commit()

    path = f"/api/v1/agent/proposals/{seeded.proposal_id}"
    projected = await client.get(path, headers=_headers(seeded.token))
    assert projected.status_code == 200
    assert projected.json()["status"] == "expired"
    assert projected.json()["allowed_actions"] == []

    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        decision = await client.post(
            f"{path}/confirm",
            headers=_headers(seeded.token),
            json=_decision_body("proposal-runtime-e2e-expired"),
        )

    assert decision.status_code == 409
    assert decision.json()["code"] == "proposal_expired"
    db_session.expire_all()
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    assert proposal is not None
    assert proposal.status == "expired"
    assert proposal.version == 2
    assert proposal.result_plan_id is None
    assert await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == seeded.user_id
        )
    ) == 1
    assert await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == seeded.user_id,
            WorkoutPlan.is_active.is_(True),
        )
    ) == 1
