from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import bcrypt
import pytest
from sqlalchemy import select

from app.config import settings
from app.models.agent import AgentConversation, AgentProposal, AgentRun
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.user import User
from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession
from app.schemas.plan_management_proposal import (
    CreatePlanAdjustmentProposalRequest,
    CreatePlanDeletionProposalRequest,
    GenericProposalDecisionRequest,
    PlanCandidate,
)
from app.services.plan_management_proposals import (
    build_plan_snapshot_v2,
    create_manual_plan_adjustment_proposal,
    create_manual_plan_deletion_proposal,
    decide_manual_plan_proposal,
    plan_snapshot_fingerprint,
)
from app.services.agent_intent import ChangeRequest, IntentResolution, IntentResolverOutcome
from app.services.auth import create_access_token


async def _seed(db_session, suffix: str):
    user = User(
        id=f"manual-plan-user-{suffix}",
        email=f"manual-plan-{suffix}@example.com",
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )
    profile = UserProfile(
        user_id=user.id,
        age=30,
        experience_level="beginner",
        training_location="gym",
        injuries=[],
        chronic_conditions=[],
        onboarding_completed=True,
    )
    squat = Exercise(
        id=f"manual-squat-{suffix}",
        name_zh="高脚杯深蹲",
        name_en=f"Manual Goblet Squat {suffix}",
        category="力量",
        difficulty="初级",
        is_active=True,
    )
    row = Exercise(
        id=f"manual-row-{suffix}",
        name_zh="坐姿划船",
        name_en=f"Manual Seated Row {suffix}",
        category="力量",
        difficulty="初级",
        is_active=True,
    )
    plan = WorkoutPlan(
        user_id=user.id,
        name="完整训练计划",
        goal="general_fitness",
        duration_weeks=4,
        days_per_week=2,
        is_active=True,
    )
    # These models intentionally do not expose ORM relationships.  Flush the
    # parent row first so PostgreSQL never observes a profile/plan foreign key
    # before its user exists.
    db_session.add(user)
    await db_session.flush()
    db_session.add_all([profile, squat, row, plan])
    await db_session.flush()
    db_session.add_all([
        PlannedExercise(
            plan_id=plan.id,
            exercise_id=squat.id,
            day_of_week=1,
            sets=3,
            reps="8-12",
            rest_seconds=90,
            order_index=0,
        ),
        PlannedExercise(
            plan_id=plan.id,
            exercise_id=row.id,
            day_of_week=4,
            sets=3,
            reps="8-12",
            rest_seconds=90,
            order_index=0,
        ),
    ])
    await db_session.commit()
    return user, plan


def _decision(proposal_id: str):
    return GenericProposalDecisionRequest(
        expected_version=1,
        client_request_id=f"manual-decision-{proposal_id}",
    )


@pytest.mark.asyncio
async def test_manual_adjustment_is_idempotent_and_preserves_unspecified_plan(db_session):
    user, plan = await _seed(db_session, "adjust")
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    candidate_data = {
        "duration_weeks": 6,
        "training_days": before.training_days,
        "exercises": [{
            key: value
            for key, value in item.model_dump().items()
            if key not in {"exercise_name", "category"}
        } for item in before.exercises],
    }
    candidate_data["exercises"][0]["sets"] = 4
    request = CreatePlanAdjustmentProposalRequest(
        client_request_id="manual-adjustment-create-0001",
        expected_base_fingerprint=plan_snapshot_fingerprint(before),
        candidate=PlanCandidate.model_validate(candidate_data),
    )
    first = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=request,
    )
    replay = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=request,
    )
    assert replay.id == first.id

    result = await decide_manual_plan_proposal(
        db_session,
        user_id=user.id,
        proposal_id=first.id,
        action="confirm",
        request=_decision(first.id),
    )
    active = await db_session.scalar(select(WorkoutPlan).where(
        WorkoutPlan.user_id == user.id, WorkoutPlan.is_active.is_(True)
    ))
    after = await build_plan_snapshot_v2(db_session, plan=active)
    await db_session.refresh(plan)
    assert result.status == "applied"
    assert active.id != plan.id
    assert plan.is_active is False
    assert after.duration_weeks == 6
    assert after.exercises[0].sets == 4
    assert after.exercises[1].model_copy(update={"item_key": before.exercises[1].item_key}) == before.exercises[1]


@pytest.mark.asyncio
async def test_new_plan_proposal_supersedes_older_pending_proposal(db_session):
    user, plan = await _seed(db_session, "supersede")
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    base = {
        "duration_weeks": before.duration_weeks,
        "training_days": before.training_days,
        "exercises": [{
            key: value
            for key, value in item.model_dump().items()
            if key not in {"exercise_name", "category"}
        } for item in before.exercises],
    }
    first_candidate = PlanCandidate.model_validate({**base, "duration_weeks": 5})
    second_candidate = PlanCandidate.model_validate({**base, "duration_weeks": 6})
    fingerprint = plan_snapshot_fingerprint(before)
    first = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=CreatePlanAdjustmentProposalRequest(
            client_request_id="manual-supersede-create-0001",
            expected_base_fingerprint=fingerprint,
            candidate=first_candidate,
        ),
    )
    second = await create_manual_plan_adjustment_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=CreatePlanAdjustmentProposalRequest(
            client_request_id="manual-supersede-create-0002",
            expected_base_fingerprint=fingerprint,
            candidate=second_candidate,
        ),
    )
    old = await db_session.get(AgentProposal, first.id)
    assert second.id != first.id
    assert old.status == "stale"
    assert old.last_error_code == "proposal_superseded"


@pytest.mark.asyncio
async def test_confirmed_plan_deletion_detaches_session_and_removes_plan(db_session):
    user, plan = await _seed(db_session, "delete")
    session = WorkoutSession(
        user_id=user.id,
        plan_id=plan.id,
        plan_name=plan.name,
        status="completed",
        trained_at=date.today(),
    )
    db_session.add(session)
    await db_session.commit()
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    proposal = await create_manual_plan_deletion_proposal(
        db_session,
        enabled=True,
        user_id=user.id,
        plan_id=plan.id,
        request=CreatePlanDeletionProposalRequest(
            client_request_id="manual-deletion-create-0001",
            expected_base_fingerprint=plan_snapshot_fingerprint(before),
        ),
    )
    await decide_manual_plan_proposal(
        db_session,
        user_id=user.id,
        proposal_id=proposal.id,
        action="confirm",
        request=_decision(proposal.id),
    )
    await db_session.refresh(session)
    assert await db_session.get(WorkoutPlan, plan.id) is None
    assert session.plan_id is None
    assert session.plan_name == "完整训练计划"


@pytest.mark.asyncio
async def test_deletion_never_orphans_an_in_progress_session(db_session):
    user, plan = await _seed(db_session, 'delete-active')
    session = WorkoutSession(user_id=user.id, plan_id=plan.id, plan_name=plan.name,
                             status='in_progress', trained_at=date.today())
    db_session.add(session)
    await db_session.commit()
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    proposal = await create_manual_plan_deletion_proposal(
        db_session, enabled=True, user_id=user.id, plan_id=plan.id,
        request=CreatePlanDeletionProposalRequest(client_request_id='block-active-delete', expected_base_fingerprint=plan_snapshot_fingerprint(before)),
    )
    # Use stable values across the service rollback which expires ORM objects.
    user_id, plan_id, session_id, proposal_id = user.id, plan.id, session.id, proposal.id
    from app.services.plan_management_proposals import PlanProposalError
    with pytest.raises(PlanProposalError) as caught:
        await decide_manual_plan_proposal(db_session, user_id=user_id, proposal_id=proposal_id,
                                          action='confirm', request=_decision(proposal_id))
    assert caught.value.code == 'workout_in_progress'
    preserved = await db_session.get(WorkoutSession, session_id)
    assert preserved.plan_id == plan_id
    assert preserved.status == 'in_progress'
    assert (await db_session.get(WorkoutPlan, plan_id)).is_active


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal_kind", ["adjustment", "deletion"])
async def test_manual_plan_proposal_requires_explicit_chat_consent(
    client, db_session, proposal_kind,
):
    user, plan = await _seed(db_session, f"consent-{proposal_kind}")
    user_id, plan_id = user.id, plan.id
    conversation = AgentConversation(user_id=user_id)
    db_session.add(conversation)
    await db_session.flush()
    conversation_id = conversation.id
    before = await build_plan_snapshot_v2(db_session, plan=plan)
    common = dict(
        client_request_id=f"consent-{proposal_kind}-create",
        expected_base_fingerprint=plan_snapshot_fingerprint(before),
    )
    if proposal_kind == "adjustment":
        request = CreatePlanAdjustmentProposalRequest(
            **common, candidate=PlanCandidate.model_validate({
                "duration_weeks": 6, "training_days": before.training_days,
                "exercises": [{
                    key: value for key, value in item.model_dump().items()
                    if key not in {"exercise_name", "category"}
                } for item in before.exercises],
            }),
        )
        reference = await create_manual_plan_adjustment_proposal(
            db_session, enabled=True, user_id=user_id, plan_id=plan_id, request=request,
            origin="agent_chat", conversation_id=conversation_id,
        )
    else:
        reference = await create_manual_plan_deletion_proposal(
            db_session, enabled=True, user_id=user_id, plan_id=plan_id,
            request=CreatePlanDeletionProposalRequest(**common),
            origin="agent_chat", conversation_id=conversation_id,
        )
    await db_session.commit()
    bogus_decision = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="general_qa", intent_domain="general", confidence=1.0,
            request_kind="proposal_decision", requested_effect="decide",
            resolved_query="确认这个提案",
            change_requests=[ChangeRequest(
                resource="general", operation="update", field_path="proposal.status",
                value="confirm",
            )],
        ), source="model",
    )
    with patch.object(settings, "MANUAL_PLAN_PROPOSALS_ENABLED", True), patch(
        "app.services.agent_runtime.resolve_intent_with_fallback",
        new=AsyncMock(return_value=bogus_decision),
    ):
        response = await client.post(
            "/api/v1/agent/chat",
            headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
            json={"message": "这个提案先别执行", "conversation_id": conversation_id},
        )
    assert response.status_code == 200
    db_session.expire_all()
    proposal = await db_session.get(AgentProposal, reference.id)
    assert proposal.status == "pending_confirmation"
    assert proposal.version == 1
    plans = list((await db_session.scalars(
        select(WorkoutPlan).where(WorkoutPlan.user_id == user_id)
    )).all())
    assert len(plans) == 1 and plans[0].id == plan_id and plans[0].is_active
    assert plans[0].duration_weeks == 4
    run = await db_session.get(AgentRun, response.json()["run_id"])
    assert run.execution_trace["termination_reason"] == "proposal_decision_not_explicit"
