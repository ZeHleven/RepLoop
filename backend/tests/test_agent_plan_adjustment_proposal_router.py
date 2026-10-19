from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.config import settings
from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan
from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentPlanSnapshot,
    PlanAdjustmentProposalPayload,
)
from app.services.agent_plan_adjustment_proposals import (
    plan_adjustment_plan_snapshot_fingerprint,
    plan_adjustment_proposal_payload_fingerprint,
)


_TEST_EXERCISE_NAME_EN = "Proposal API Test Goblet Squat"


@dataclass(frozen=True)
class _ApiSeed:
    token: str
    user_id: str
    proposal_id: str
    base_plan_id: str
    exercise_id: str
    payload_fingerprint: str


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def _cleanup_proposal_api_fixtures(session_factory):
    yield
    user_ids = select(User.id).where(
        User.email.like("proposal-api-%@example.com")
    )
    plan_ids = select(WorkoutPlan.id).where(
        WorkoutPlan.user_id.in_(user_ids)
    )
    async with session_factory() as session:
        await session.execute(delete(PlannedExercise).where(
            PlannedExercise.plan_id.in_(plan_ids)
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
        await session.execute(delete(UserProfile).where(
            UserProfile.user_id.in_(user_ids)
        ))
        await session.execute(delete(WorkoutPlan).where(
            WorkoutPlan.user_id.in_(user_ids)
        ))
        await session.execute(delete(User).where(User.id.in_(user_ids)))
        await session.execute(delete(Exercise).where(
            Exercise.name_en == _TEST_EXERCISE_NAME_EN
        ))
        await session.commit()


async def _token(client, suffix: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"proposal-api-{suffix}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201
    return response.json()["access_token"]


def _snapshot(
    *,
    exercise_id: str,
    sets: int,
) -> PlanAdjustmentPlanSnapshot:
    return PlanAdjustmentPlanSnapshot.model_validate({
        "name": "基础力量计划",
        "goal": "strength",
        "duration_weeks": 4,
        "days_per_week": 1,
        "exercises": [{
            "slot_key": "day-1-order-0",
            "exercise_id": exercise_id,
            "exercise_name": "高脚杯深蹲",
            "day_of_week": 1,
            "sets": sets,
            "reps": "8-10",
            "rest_seconds": 120,
            "recommended_weight_kg": 20.0,
            "order_index": 0,
        }],
    })


async def _seed_pending_proposal(
    client,
    db_session,
    *,
    suffix: str,
) -> _ApiSeed:
    token = await _token(client, suffix)
    user = await db_session.scalar(
        select(User).where(
            User.email == f"proposal-api-{suffix}@example.com"
        )
    )
    assert user is not None

    exercise_id = (
        "api" + hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:7]
    )
    exercise = Exercise(
        id=exercise_id,
        name_zh="高脚杯深蹲",
        name_en=_TEST_EXERCISE_NAME_EN,
        category="strength",
        muscle_primary=["quadriceps"],
        muscle_secondary=[],
        equipment=["dumbbell"],
        difficulty="中级",
        movement_pattern="squat",
        contraindications=[],
        is_active=True,
    )
    profile = await db_session.scalar(
        select(UserProfile).where(UserProfile.user_id == user.id)
    )
    assert profile is not None
    profile.experience_level = "intermediate"
    profile.primary_goal = "strength"
    profile.training_days_per_week = 1
    profile.training_location = "gym"
    profile.injuries = []
    profile.chronic_conditions = []
    profile.onboarding_completed = True
    base_plan = WorkoutPlan(
        id=f"proposal-api-plan-{suffix}",
        user_id=user.id,
        name="基础力量计划",
        goal="strength",
        duration_weeks=4,
        days_per_week=1,
        is_active=True,
        ai_generated=False,
        notes="API 原子应用测试",
    )
    conversation = AgentConversation(
        id=f"proposal-api-conversation-{suffix}",
        user_id=user.id,
    )
    db_session.add_all([exercise, base_plan, conversation])
    await db_session.flush()

    planned = PlannedExercise(
        plan_id=base_plan.id,
        exercise_id=exercise.id,
        day_of_week=1,
        sets=4,
        reps="8-10",
        rest_seconds=120,
        recommended_weight_kg=20.0,
        order_index=0,
    )
    run = AgentRun(
        id=f"proposal-api-run-{suffix}",
        conversation_id=conversation.id,
        user_id=user.id,
        status="completed",
    )
    db_session.add_all([planned, run])
    await db_session.flush()

    before = _snapshot(exercise_id=exercise.id, sets=4)
    after = _snapshot(exercise_id=exercise.id, sets=3)
    base_fingerprint = plan_adjustment_plan_snapshot_fingerprint(before)
    now = datetime.now(timezone.utc)
    payload = PlanAdjustmentProposalPayload(
        schema_version="1.0.0",
        proposal_type="plan_adjustment_v1",
        target={
            "resource_type": "workout_plan",
            "base_plan_id": base_plan.id,
            "base_plan_fingerprint": base_fingerprint,
        },
        before=before,
        after=after,
        changes=[{
            "change_type": "adjust_exercise_target",
            "stable_display_key": "day-1-order-0",
            "before": {"sets": 4},
            "after": {"sets": 3},
            "reason": "近期完成率偏低，先保守降低一组。",
            "safety_priority": False,
        }],
        evidence=[{
            "tool_id": "plan.get_active",
            "result_fingerprint": "a" * 64,
            "observed_at": now - timedelta(minutes=2),
        }, {
            "tool_id": "workout.get_progress",
            "result_fingerprint": "b" * 64,
            "observed_at": now - timedelta(minutes=1),
        }],
        rationale=["降低训练量以提高连续完成概率。"],
        safety_notes=[],
    )
    payload_fingerprint = plan_adjustment_proposal_payload_fingerprint(
        payload
    )
    proposal = AgentProposal(
        id=f"proposal-api-{suffix}",
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        proposal_type="plan_adjustment_v1",
        payload_data=payload.model_dump(mode="json", exclude_unset=True),
        payload_fingerprint=payload_fingerprint,
        base_plan_id=base_plan.id,
        base_plan_fingerprint=base_fingerprint,
        status="pending_confirmation",
        version=1,
        expires_at=now + timedelta(hours=24),
        created_at=now,
        updated_at=now,
    )
    db_session.add(proposal)
    await db_session.commit()
    return _ApiSeed(
        token=token,
        user_id=user.id,
        proposal_id=proposal.id,
        base_plan_id=base_plan.id,
        exercise_id=exercise.id,
        payload_fingerprint=payload_fingerprint,
    )


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _decision_body(request_id: str) -> dict[str, object]:
    return {
        "expected_version": 1,
        "client_request_id": request_id,
    }


@pytest.mark.asyncio
async def test_proposal_routes_require_authentication(client):
    read = await client.get("/api/v1/agent/proposals/missing")
    confirm = await client.post(
        "/api/v1/agent/proposals/missing/confirm",
        json=_decision_body("proposal-api-auth-confirm"),
    )
    reject = await client.post(
        "/api/v1/agent/proposals/missing/reject",
        json=_decision_body("proposal-api-auth-reject"),
    )

    assert {read.status_code, confirm.status_code, reject.status_code} == {
        403
    }


@pytest.mark.asyncio
async def test_read_and_decisions_hide_foreign_proposals_as_missing(
    client,
    db_session,
):
    seeded = await _seed_pending_proposal(
        client,
        db_session,
        suffix="ownership",
    )
    foreign_token = await _token(client, "ownership-foreign")

    owned = await client.get(
        f"/api/v1/agent/proposals/{seeded.proposal_id}",
        headers=_headers(seeded.token),
    )
    foreign = await client.get(
        f"/api/v1/agent/proposals/{seeded.proposal_id}",
        headers=_headers(foreign_token),
    )
    missing = await client.get(
        "/api/v1/agent/proposals/missing-proposal",
        headers=_headers(foreign_token),
    )
    foreign_confirm = await client.post(
        f"/api/v1/agent/proposals/{seeded.proposal_id}/confirm",
        headers=_headers(foreign_token),
        json=_decision_body("proposal-api-foreign-confirm"),
    )
    foreign_reject = await client.post(
        f"/api/v1/agent/proposals/{seeded.proposal_id}/reject",
        headers=_headers(foreign_token),
        json=_decision_body("proposal-api-foreign-reject"),
    )

    assert owned.status_code == 200
    assert owned.json()["status"] == "pending_confirmation"
    assert owned.json()["allowed_actions"] == ["confirm", "reject"]
    assert owned.json()["payload_fingerprint"] == seeded.payload_fingerprint
    assert owned.json()["payload"]["changes"][0]["before"] == {"sets": 4}
    assert owned.json()["payload"]["changes"][0]["after"] == {"sets": 3}
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign_confirm.status_code == 404
    assert foreign_reject.status_code == 404
    assert foreign.json() == missing.json() == {
        "code": "proposal_not_found",
        "message": "调整提案不存在或已不可访问。",
    }
    assert foreign_confirm.json() == foreign_reject.json() == foreign.json()
    assert await db_session.scalar(select(AgentProposal.status).where(
        AgentProposal.id == seeded.proposal_id
    )) == "pending_confirmation"


@pytest.mark.asyncio
async def test_confirm_applies_plan_atomically_and_replays_exact_result(
    client,
    db_session,
):
    seeded = await _seed_pending_proposal(
        client,
        db_session,
        suffix="confirm",
    )
    path = f"/api/v1/agent/proposals/{seeded.proposal_id}/confirm"
    headers = _headers(seeded.token)

    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        first = await client.post(
            path,
            headers=headers,
            json=_decision_body("proposal-api-confirm-first"),
        )
        same_request = await client.post(
            path,
            headers=headers,
            json=_decision_body("proposal-api-confirm-first"),
        )
        different_request = await client.post(
            path,
            headers=headers,
            json=_decision_body("proposal-api-confirm-second"),
        )

    assert first.status_code == 200
    assert same_request.status_code == 200
    assert different_request.status_code == 200
    assert first.json() == same_request.json() == different_request.json()
    assert first.json()["status"] == "applied"
    assert first.json()["applied"] is True
    assert first.json()["result_plan_id"] != seeded.base_plan_id

    plans = list((await db_session.execute(
        select(WorkoutPlan).where(WorkoutPlan.user_id == seeded.user_id)
    )).scalars().all())
    active = [plan for plan in plans if plan.is_active]
    assert len(plans) == 2
    assert len(active) == 1
    assert active[0].id == first.json()["result_plan_id"]
    applied_sets = await db_session.scalar(
        select(PlannedExercise.sets).where(
            PlannedExercise.plan_id == active[0].id
        )
    )
    assert applied_sets == 3
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    await db_session.refresh(proposal)
    assert proposal.status == "applied"
    assert proposal.version == 2
    assert proposal.result_plan_id == active[0].id


@pytest.mark.asyncio
async def test_reject_is_idempotent_and_never_changes_plans(
    client,
    db_session,
):
    seeded = await _seed_pending_proposal(
        client,
        db_session,
        suffix="reject",
    )
    path = f"/api/v1/agent/proposals/{seeded.proposal_id}/reject"
    body = _decision_body("proposal-api-reject-request")

    with patch.object(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    ):
        first = await client.post(
            path,
            headers=_headers(seeded.token),
            json=body,
        )
        replay = await client.post(
            path,
            headers=_headers(seeded.token),
            json=body,
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert first.json() == replay.json()
    assert first.json()["status"] == "rejected"
    assert first.json()["applied"] is False
    plans = list((await db_session.execute(
        select(WorkoutPlan).where(WorkoutPlan.user_id == seeded.user_id)
    )).scalars().all())
    assert len(plans) == 1
    assert plans[0].id == seeded.base_plan_id
    assert plans[0].is_active is True


@pytest.mark.asyncio
async def test_disabled_flag_and_identity_injection_are_write_free(
    client,
    db_session,
):
    seeded = await _seed_pending_proposal(
        client,
        db_session,
        suffix="disabled",
    )
    path = f"/api/v1/agent/proposals/{seeded.proposal_id}/confirm"
    disabled = await client.post(
        path,
        headers=_headers(seeded.token),
        json=_decision_body("proposal-api-disabled"),
    )
    injected_body = _decision_body("proposal-api-injected-user")
    injected_body["user_id"] = "attacker-controlled-user"
    injected = await client.post(
        path,
        headers=_headers(seeded.token),
        json=injected_body,
    )

    assert disabled.status_code == 503
    assert disabled.json()["code"] == "proposal_feature_disabled"
    assert injected.status_code == 422
    assert await db_session.scalar(select(AgentProposal.status).where(
        AgentProposal.id == seeded.proposal_id
    )) == "pending_confirmation"
    assert await db_session.scalar(select(func.count(WorkoutPlan.id)).where(
        WorkoutPlan.user_id == seeded.user_id
    )) == 1


@pytest.mark.asyncio
async def test_confirm_midpoint_failure_rolls_back_plan_switch_over_http(
    client,
    db_session,
):
    seeded = await _seed_pending_proposal(
        client,
        db_session,
        suffix="rollback",
    )

    def fail_after_switch(_injector, stage: str) -> None:
        if stage == "active_plan_switched":
            raise RuntimeError("fixture failure after active switch")

    with (
        patch.object(
            settings,
            "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
            True,
        ),
        patch(
            "app.services.agent_plan_adjustment_proposal_execution._inject_fault",
            side_effect=fail_after_switch,
        ),
    ):
        response = await client.post(
            f"/api/v1/agent/proposals/{seeded.proposal_id}/confirm",
            headers=_headers(seeded.token),
            json=_decision_body("proposal-api-rollback"),
        )

    assert response.status_code == 500
    assert response.json()["code"] == "proposal_execution_failed"
    plans = list((await db_session.execute(
        select(WorkoutPlan).where(WorkoutPlan.user_id == seeded.user_id)
    )).scalars().all())
    assert len(plans) == 1
    assert plans[0].id == seeded.base_plan_id
    assert plans[0].is_active is True
    proposal = await db_session.get(AgentProposal, seeded.proposal_id)
    await db_session.refresh(proposal)
    assert proposal.status == "failed"
    assert proposal.result_plan_id is None


@pytest.mark.asyncio
async def test_concurrent_confirm_requests_create_only_one_plan(client, db_session):
    seeded = await _seed_pending_proposal(
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
                json=_decision_body("proposal-api-concurrent-first"),
            ),
            client.post(
                path,
                headers=headers,
                json=_decision_body("proposal-api-concurrent-second"),
            ),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert await db_session.scalar(select(func.count(WorkoutPlan.id)).where(
        WorkoutPlan.user_id == seeded.user_id
    )) == 2
    assert await db_session.scalar(select(func.count(WorkoutPlan.id)).where(
        WorkoutPlan.user_id == seeded.user_id,
        WorkoutPlan.is_active.is_(True),
    )) == 1
