from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select

from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan
from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentPlanSnapshot,
    PlanAdjustmentProposalPayload,
)
from app.schemas.agent_plan_adjustment_proposal_api import (
    PlanAdjustmentProposalDecisionRequest,
)
from app.services.agent_plan_adjustment_proposal_execution import (
    apply_confirmed_plan_adjustment_atomically,
)
from app.services.agent_plan_adjustment_proposals import (
    plan_adjustment_plan_snapshot_fingerprint,
    plan_adjustment_proposal_payload_fingerprint,
)


_NOW = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)
_TEST_EXERCISE_NAME_EN = "Atomic Proposal Test Goblet Squat"


@pytest_asyncio.fixture(scope="module", loop_scope="session", autouse=True)
async def _cleanup_execution_fixtures(session_factory):
    yield
    user_ids = select(User.id).where(User.id.like("execution-user-%"))
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


@dataclass(frozen=True)
class _SeededExecution:
    user: User
    profile: UserProfile
    exercise: Exercise
    base_plan: WorkoutPlan
    planned_exercise: PlannedExercise
    proposal: AgentProposal
    before: PlanAdjustmentPlanSnapshot
    after: PlanAdjustmentPlanSnapshot


def _snapshot(
    *,
    plan_id: str,
    exercise_id: str,
    exercise_name: str,
    sets: int,
    duration_weeks: int = 4,
) -> PlanAdjustmentPlanSnapshot:
    del plan_id
    return PlanAdjustmentPlanSnapshot.model_validate({
        "name": "基础力量计划",
        "goal": "strength",
        "duration_weeks": duration_weeks,
        "days_per_week": 1,
        "exercises": [{
            "slot_key": "day-1-order-0",
            "exercise_id": exercise_id,
            "exercise_name": exercise_name,
            "day_of_week": 1,
            "sets": sets,
            "reps": "8-10",
            "rest_seconds": 120,
            "recommended_weight_kg": 20.0,
            "order_index": 0,
        }],
    })


def _payload(
    *,
    plan_id: str,
    before: PlanAdjustmentPlanSnapshot,
    after: PlanAdjustmentPlanSnapshot,
) -> PlanAdjustmentProposalPayload:
    return PlanAdjustmentProposalPayload(
        schema_version="1.0.0",
        proposal_type="plan_adjustment_v1",
        target={
            "resource_type": "workout_plan",
            "base_plan_id": plan_id,
            "base_plan_fingerprint": (
                plan_adjustment_plan_snapshot_fingerprint(before)
            ),
        },
        before=before,
        after=after,
        changes=[{
            "change_type": "adjust_exercise_target",
            "stable_display_key": "day-1-order-0",
            "before": {"sets": before.exercises[0].sets},
            "after": {"sets": after.exercises[0].sets},
            "reason": "近期完成率偏低，先降低一组。",
            "safety_priority": False,
        }],
        evidence=[
            {
                "tool_id": "plan.get_active",
                "result_fingerprint": "a" * 64,
                "observed_at": _NOW - timedelta(hours=1),
            },
            {
                "tool_id": "workout.get_progress",
                "result_fingerprint": "b" * 64,
                "observed_at": _NOW - timedelta(hours=1),
            },
        ],
        rationale=["降低单次训练量以提高连续完成概率。"],
        safety_notes=[],
    )


async def _seed_execution(
    db_session,
    *,
    suffix: str,
) -> _SeededExecution:
    exercise_id = hashlib.sha256(suffix.encode("utf-8")).hexdigest()[:10]
    user = User(
        id=f"execution-user-{suffix}",
        email=f"execution-{suffix}@example.com",
        password_hash="not-used-by-this-test",
    )
    profile = UserProfile(
        id=f"execution-profile-{suffix}",
        user_id=user.id,
        experience_level="intermediate",
        primary_goal="strength",
        training_days_per_week=1,
        training_location="gym",
        injuries=[],
        chronic_conditions=[],
        onboarding_completed=True,
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
    base_plan = WorkoutPlan(
        id=f"execution-plan-{suffix}",
        user_id=user.id,
        name="基础力量计划",
        goal="strength",
        duration_weeks=4,
        days_per_week=1,
        is_active=True,
        ai_generated=False,
        notes="保留的用户计划备注",
    )
    planned_exercise = PlannedExercise(
        id=f"execution-planned-{suffix}",
        plan_id=base_plan.id,
        exercise_id=exercise.id,
        day_of_week=1,
        sets=4,
        reps="8-10",
        rest_seconds=120,
        recommended_weight_kg=20.0,
        order_index=0,
    )
    conversation = AgentConversation(
        id=f"execution-conversation-{suffix}",
        user_id=user.id,
    )
    run = AgentRun(
        id=f"execution-run-{suffix}",
        conversation_id=conversation.id,
        user_id=user.id,
        status="completed",
    )
    before = _snapshot(
        plan_id=base_plan.id,
        exercise_id=exercise.id,
        exercise_name=exercise.name_zh,
        sets=4,
    )
    after = _snapshot(
        plan_id=base_plan.id,
        exercise_id=exercise.id,
        exercise_name=exercise.name_zh,
        sets=3,
    )
    payload = _payload(
        plan_id=base_plan.id,
        before=before,
        after=after,
    )
    proposal = AgentProposal(
        id=f"execution-proposal-{suffix}",
        user_id=user.id,
        conversation_id=conversation.id,
        run_id=run.id,
        proposal_type="plan_adjustment_v1",
        payload_data=payload.model_dump(mode="json", exclude_unset=True),
        payload_fingerprint=plan_adjustment_proposal_payload_fingerprint(
            payload
        ),
        base_plan_id=base_plan.id,
        base_plan_fingerprint=(
            plan_adjustment_plan_snapshot_fingerprint(before)
        ),
        status="pending_confirmation",
        version=1,
        expires_at=_NOW + timedelta(hours=23),
        created_at=_NOW - timedelta(hours=1),
        updated_at=_NOW - timedelta(hours=1),
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add_all([profile, exercise, base_plan, conversation])
    await db_session.flush()
    db_session.add_all([planned_exercise, run])
    await db_session.flush()
    db_session.add(proposal)
    await db_session.flush()
    return _SeededExecution(
        user=user,
        profile=profile,
        exercise=exercise,
        base_plan=base_plan,
        planned_exercise=planned_exercise,
        proposal=proposal,
        before=before,
        after=after,
    )


def _request(client_request_id: str = "execution-confirm-0001"):
    return PlanAdjustmentProposalDecisionRequest(
        expected_version=1,
        client_request_id=client_request_id,
    )


@pytest.mark.asyncio
async def test_confirm_atomically_versions_plan_and_marks_proposal_applied(
    db_session,
    session_factory,
):
    seeded = await _seed_execution(db_session, suffix="success")
    await db_session.commit()

    result = await apply_confirmed_plan_adjustment_atomically(
        db_session,
        enabled=True,
        user_id=seeded.user.id,
        proposal_id=seeded.proposal.id,
        request=_request(),
        now=_NOW,
    )

    assert result.outcome == "completed"
    assert result.response is not None
    assert result.response.status == "applied"
    assert result.response.version == 2
    assert result.response.applied is True
    assert result.response.result_plan_id is not None
    assert result.response.result_plan_fingerprint == (
        plan_adjustment_plan_snapshot_fingerprint(seeded.after)
    )
    active_plans = list((await db_session.execute(
        select(WorkoutPlan).where(
            WorkoutPlan.user_id == seeded.user.id,
            WorkoutPlan.is_active.is_(True),
        )
    )).scalars().all())
    assert len(active_plans) == 1
    assert active_plans[0].id == result.response.result_plan_id
    assert active_plans[0].id != seeded.base_plan.id
    assert active_plans[0].name == seeded.after.name
    assert active_plans[0].notes == seeded.base_plan.notes
    assert seeded.base_plan.is_active is False
    new_exercises = list((await db_session.execute(
        select(PlannedExercise).where(
            PlannedExercise.plan_id == active_plans[0].id
        )
    )).scalars().all())
    assert len(new_exercises) == 1
    assert new_exercises[0].sets == 3
    assert new_exercises[0].exercise_id == seeded.exercise.id
    assert seeded.proposal.status == "applied"
    assert seeded.proposal.decision_action == "confirm"
    assert seeded.proposal.confirmed_at == _NOW
    assert seeded.proposal.applied_at == _NOW

    async with session_factory() as outside:
        outside_status = await outside.scalar(
            select(AgentProposal.status).where(
                AgentProposal.id == seeded.proposal.id
            )
        )
        outside_active_id = await outside.scalar(
            select(WorkoutPlan.id).where(
                WorkoutPlan.user_id == seeded.user.id,
                WorkoutPlan.is_active.is_(True),
            )
        )
    assert outside_status == "pending_confirmation"
    assert outside_active_id == seeded.base_plan.id

    await db_session.commit()
    async with session_factory() as outside:
        durable_status = await outside.scalar(
            select(AgentProposal.status).where(
                AgentProposal.id == seeded.proposal.id
            )
        )
        durable_active_ids = list((await outside.execute(
            select(WorkoutPlan.id).where(
                WorkoutPlan.user_id == seeded.user.id,
                WorkoutPlan.is_active.is_(True),
            )
        )).scalars().all())
    assert durable_status == "applied"
    assert durable_active_ids == [result.response.result_plan_id]


@pytest.mark.asyncio
async def test_mid_transaction_failure_rolls_back_candidate_and_plan_switch(
    db_session,
    session_factory,
):
    seeded = await _seed_execution(db_session, suffix="rollback")
    user_id = seeded.user.id
    proposal_id = seeded.proposal.id
    base_plan_id = seeded.base_plan.id
    await db_session.commit()

    def fail_after_switch(stage: str) -> None:
        if stage == "active_plan_switched":
            raise RuntimeError("fixture failure after active switch")

    result = await apply_confirmed_plan_adjustment_atomically(
        db_session,
        enabled=True,
        user_id=user_id,
        proposal_id=proposal_id,
        request=_request("execution-confirm-rollback"),
        now=_NOW,
        fault_injector=fail_after_switch,
    )

    assert result.error is not None
    assert result.error.code == "proposal_execution_failed"
    assert result.state_changed is True
    active_ids = list((await db_session.execute(
        select(WorkoutPlan.id).where(
            WorkoutPlan.user_id == user_id,
            WorkoutPlan.is_active.is_(True),
        )
    )).scalars().all())
    plan_count = await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == user_id
        )
    )
    assert active_ids == [base_plan_id]
    assert plan_count == 1
    await db_session.refresh(seeded.proposal)
    assert seeded.proposal.status == "failed"
    assert seeded.proposal.version == 2
    assert seeded.proposal.result_plan_id is None
    assert seeded.proposal.last_error_code == "proposal_execution_failed"

    await db_session.commit()
    async with session_factory() as outside:
        durable_plan_count = await outside.scalar(
            select(func.count(WorkoutPlan.id)).where(
                WorkoutPlan.user_id == user_id
            )
        )
        durable_active_id = await outside.scalar(
            select(WorkoutPlan.id).where(
                WorkoutPlan.user_id == user_id,
                WorkoutPlan.is_active.is_(True),
            )
        )
        durable_proposal = await outside.get(
            AgentProposal,
            proposal_id,
        )
    assert durable_plan_count == 1
    assert durable_active_id == base_plan_id
    assert durable_proposal is not None
    assert durable_proposal.status == "failed"


@pytest.mark.asyncio
async def test_changed_base_snapshot_marks_proposal_stale_without_new_plan(
    db_session,
):
    seeded = await _seed_execution(db_session, suffix="base-changed")
    user_id = seeded.user.id
    proposal_id = seeded.proposal.id
    base_plan_id = seeded.base_plan.id
    seeded.planned_exercise.sets = 5
    await db_session.commit()

    result = await apply_confirmed_plan_adjustment_atomically(
        db_session,
        enabled=True,
        user_id=user_id,
        proposal_id=proposal_id,
        request=_request("execution-confirm-base-changed"),
        now=_NOW,
    )

    assert result.error is not None
    assert result.error.code == "proposal_base_plan_changed"
    await db_session.refresh(seeded.proposal)
    assert seeded.proposal.status == "stale"
    assert seeded.proposal.version == 2
    assert seeded.proposal.decision_action == "confirm"
    assert await db_session.scalar(select(WorkoutPlan.is_active).where(
        WorkoutPlan.id == base_plan_id
    )) is True
    plan_count = await db_session.scalar(
        select(func.count(WorkoutPlan.id)).where(
            WorkoutPlan.user_id == user_id
        )
    )
    assert plan_count == 1


@pytest.mark.asyncio
async def test_inactive_exercise_marks_candidate_unavailable(db_session):
    seeded = await _seed_execution(db_session, suffix="inactive-exercise")
    user_id = seeded.user.id
    proposal_id = seeded.proposal.id
    base_plan_id = seeded.base_plan.id
    seeded.exercise.is_active = False
    await db_session.commit()

    result = await apply_confirmed_plan_adjustment_atomically(
        db_session,
        enabled=True,
        user_id=user_id,
        proposal_id=proposal_id,
        request=_request("execution-confirm-inactive"),
        now=_NOW,
    )

    assert result.error is not None
    assert result.error.code == "proposal_candidate_unavailable"
    await db_session.refresh(seeded.proposal)
    assert seeded.proposal.status == "stale"
    assert await db_session.scalar(select(WorkoutPlan.is_active).where(
        WorkoutPlan.id == base_plan_id
    )) is True


@pytest.mark.asyncio
async def test_latest_health_incompatibility_blocks_application(db_session):
    seeded = await _seed_execution(db_session, suffix="health-changed")
    user_id = seeded.user.id
    proposal_id = seeded.proposal.id
    base_plan_id = seeded.base_plan.id
    seeded.profile.injuries = ["膝关节"]
    await db_session.commit()

    result = await apply_confirmed_plan_adjustment_atomically(
        db_session,
        enabled=True,
        user_id=user_id,
        proposal_id=proposal_id,
        request=_request("execution-confirm-health"),
        now=_NOW,
    )

    assert result.error is not None
    assert result.error.code == "proposal_health_context_changed"
    await db_session.refresh(seeded.proposal)
    assert seeded.proposal.status == "stale"
    assert seeded.proposal.last_error_code == (
        "proposal_health_context_changed"
    )
    assert await db_session.scalar(select(WorkoutPlan.is_active).where(
        WorkoutPlan.id == base_plan_id
    )) is True


@pytest.mark.asyncio
async def test_undeclared_candidate_change_is_rejected_by_first_cohort(
    db_session,
):
    seeded = await _seed_execution(db_session, suffix="invalid-candidate")
    user_id = seeded.user.id
    proposal_id = seeded.proposal.id
    base_plan_id = seeded.base_plan.id
    payload_data = dict(seeded.proposal.payload_data)
    payload_data["after"] = dict(payload_data["after"])
    payload_data["after"]["name"] = "模型未声明的计划改名"
    changed_payload = PlanAdjustmentProposalPayload.model_validate(
        payload_data
    )
    seeded.proposal.payload_data = changed_payload.model_dump(
        mode="json",
        exclude_unset=True,
    )
    seeded.proposal.payload_fingerprint = (
        plan_adjustment_proposal_payload_fingerprint(changed_payload)
    )
    await db_session.commit()

    result = await apply_confirmed_plan_adjustment_atomically(
        db_session,
        enabled=True,
        user_id=user_id,
        proposal_id=proposal_id,
        request=_request("execution-confirm-invalid-candidate"),
        now=_NOW,
    )

    assert result.error is not None
    assert result.error.code == "proposal_payload_invalid"
    await db_session.refresh(seeded.proposal)
    assert seeded.proposal.status == "stale"
    assert await db_session.scalar(select(WorkoutPlan.is_active).where(
        WorkoutPlan.id == base_plan_id
    )) is True


@pytest.mark.asyncio
async def test_concurrent_confirms_create_one_plan_and_share_result(
    db_session,
    session_factory,
):
    seeded = await _seed_execution(db_session, suffix="concurrent")
    user_id = seeded.user.id
    proposal_id = seeded.proposal.id
    base_plan_id = seeded.base_plan.id
    await db_session.commit()

    async def confirm(client_request_id: str):
        async with session_factory() as session:
            result = await apply_confirmed_plan_adjustment_atomically(
                session,
                enabled=True,
                user_id=user_id,
                proposal_id=proposal_id,
                request=_request(client_request_id),
                now=_NOW,
            )
            await session.commit()
            return result

    first, second = await asyncio.gather(
        confirm("execution-concurrent-a"),
        confirm("execution-concurrent-b"),
    )

    assert {first.outcome, second.outcome} == {"completed", "replayed"}
    assert first.response is not None
    assert second.response is not None
    assert first.response == second.response
    async with session_factory() as session:
        plan_count = await session.scalar(
            select(func.count(WorkoutPlan.id)).where(
                WorkoutPlan.user_id == user_id
            )
        )
        active_ids = list((await session.execute(
            select(WorkoutPlan.id).where(
                WorkoutPlan.user_id == user_id,
                WorkoutPlan.is_active.is_(True),
            )
        )).scalars().all())
        proposal = await session.get(AgentProposal, proposal_id)
    assert plan_count == 2
    assert len(active_ids) == 1
    assert active_ids[0] != base_plan_id
    assert proposal is not None
    assert proposal.status == "applied"
    assert proposal.version == 2
