"""Atomic, deterministic application of confirmed plan adjustments."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentProposal
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.workout import PlannedExercise, WorkoutPlan
from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentPlanSnapshot,
    PlanAdjustmentProposalDraft,
    PlanAdjustmentProposalPayload,
)
from app.schemas.agent_plan_adjustment_proposal_api import (
    PlanAdjustmentProposalBusinessErrorCode,
    PlanAdjustmentProposalDecisionRequest,
)
from app.services.agent_plan_adjustment_proposal_decisions import (
    PlanAdjustmentProposalDecisionServiceResult,
    build_plan_adjustment_proposal_decision_response,
    decide_plan_adjustment_proposal,
    proposal_business_error_result,
    query_owned_plan_adjustment_proposal,
)
from app.services.agent_plan_adjustment_proposals import (
    apply_plan_adjustment_proposal_draft,
    plan_adjustment_plan_snapshot_fingerprint,
)
from app.services.personalized_planner import (
    PersonalizedPlanError,
    validate_personalized_selection,
)


PlanAdjustmentExecutionStage = Literal[
    "candidate_persisted",
    "active_plan_switched",
    "proposal_applied",
]
PlanAdjustmentExecutionFaultInjector = Callable[
    [PlanAdjustmentExecutionStage],
    None,
]


class _PlanAdjustmentExecutionRejected(RuntimeError):
    def __init__(
        self,
        code: PlanAdjustmentProposalBusinessErrorCode,
    ) -> None:
        self.code = code
        super().__init__(code)


def _reject(code: PlanAdjustmentProposalBusinessErrorCode) -> None:
    raise _PlanAdjustmentExecutionRejected(code)


def _inject_fault(
    injector: PlanAdjustmentExecutionFaultInjector | None,
    stage: PlanAdjustmentExecutionStage,
) -> None:
    if injector is not None:
        injector(stage)


async def _locked_active_plan(
    db: AsyncSession,
    *,
    user_id: str,
    expected_plan_id: str,
) -> WorkoutPlan:
    active_plans = list((await db.execute(
        select(WorkoutPlan)
        .where(
            WorkoutPlan.user_id == user_id,
            WorkoutPlan.is_active.is_(True),
        )
        .order_by(WorkoutPlan.id)
        .with_for_update()
    )).scalars().all())
    if len(active_plans) != 1 or active_plans[0].id != expected_plan_id:
        _reject("proposal_base_plan_changed")
    return active_plans[0]


async def _locked_plan_snapshot(
    db: AsyncSession,
    *,
    plan: WorkoutPlan,
) -> tuple[PlanAdjustmentPlanSnapshot, list[Exercise]]:
    planned = list((await db.execute(
        select(PlannedExercise)
        .where(PlannedExercise.plan_id == plan.id)
        .order_by(
            PlannedExercise.day_of_week,
            PlannedExercise.order_index,
        )
        .with_for_update()
    )).scalars().all())
    if not planned:
        _reject("proposal_base_plan_changed")

    exercise_ids = {item.exercise_id for item in planned}
    exercises = list((await db.execute(
        select(Exercise)
        .where(Exercise.id.in_(exercise_ids))
        .order_by(Exercise.id)
        .with_for_update()
    )).scalars().all())
    if len(exercises) != len(exercise_ids) or any(
        not item.is_active for item in exercises
    ):
        _reject("proposal_candidate_unavailable")
    names = {item.id: item.name_zh for item in exercises}
    try:
        snapshot = PlanAdjustmentPlanSnapshot.model_validate({
            "name": plan.name,
            "goal": plan.goal,
            "duration_weeks": plan.duration_weeks,
            "days_per_week": plan.days_per_week,
            "exercises": [{
                "slot_key": (
                    f"day-{item.day_of_week}-order-{item.order_index}"
                ),
                "exercise_id": item.exercise_id,
                "exercise_name": names[item.exercise_id],
                "day_of_week": item.day_of_week,
                "sets": item.sets,
                "reps": item.reps,
                "rest_seconds": item.rest_seconds,
                "recommended_weight_kg": item.recommended_weight_kg,
                "order_index": item.order_index,
            } for item in planned],
        })
    except (KeyError, ValidationError) as exc:
        raise _PlanAdjustmentExecutionRejected(
            "proposal_base_plan_changed"
        ) from exc
    return snapshot, exercises


def _validated_first_cohort_candidate(
    payload: PlanAdjustmentProposalPayload,
) -> PlanAdjustmentPlanSnapshot:
    try:
        draft = PlanAdjustmentProposalDraft(
            proposal_type=payload.proposal_type,
            changes=payload.changes,
            rationale=payload.rationale,
            safety_notes=payload.safety_notes,
        )
        derived = apply_plan_adjustment_proposal_draft(
            payload.before,
            draft,
        )
    except (TypeError, ValueError, ValidationError) as exc:
        raise _PlanAdjustmentExecutionRejected(
            "proposal_payload_invalid"
        ) from exc
    if derived != payload.after:
        _reject("proposal_payload_invalid")
    return derived


async def _validate_latest_health_context(
    db: AsyncSession,
    *,
    user_id: str,
    exercises: list[Exercise],
) -> None:
    profile = await db.scalar(
        select(UserProfile)
        .where(UserProfile.user_id == user_id)
        .with_for_update()
    )
    if profile is None or not profile.onboarding_completed:
        _reject("proposal_health_context_changed")
    try:
        validate_personalized_selection(profile, exercises)
    except PersonalizedPlanError as exc:
        raise _PlanAdjustmentExecutionRejected(
            "proposal_health_context_changed"
        ) from exc


async def _persist_candidate_plan(
    db: AsyncSession,
    *,
    user_id: str,
    base_plan: WorkoutPlan,
    candidate: PlanAdjustmentPlanSnapshot,
) -> WorkoutPlan:
    new_plan = WorkoutPlan(
        user_id=user_id,
        family_id=base_plan.family_id,
        name=candidate.name,
        goal=candidate.goal,
        duration_weeks=candidate.duration_weeks,
        days_per_week=candidate.days_per_week,
        is_active=False,
        ai_generated=True,
        notes=base_plan.notes,
    )
    db.add(new_plan)
    await db.flush()
    db.add_all([
        PlannedExercise(
            plan_id=new_plan.id,
            exercise_id=item.exercise_id,
            day_of_week=item.day_of_week,
            sets=item.sets,
            reps=item.reps,
            rest_seconds=item.rest_seconds,
            recommended_weight_kg=item.recommended_weight_kg,
            order_index=item.order_index,
        )
        for item in candidate.exercises
    ])
    await db.flush()
    return new_plan


async def _record_confirmation_error(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    request: PlanAdjustmentProposalDecisionRequest,
    now: datetime,
    code: PlanAdjustmentProposalBusinessErrorCode,
) -> PlanAdjustmentProposalDecisionServiceResult:
    proposal = await query_owned_plan_adjustment_proposal(
        db,
        user_id=user_id,
        proposal_id=proposal_id,
        for_update=True,
    )
    if (
        proposal is None
        or proposal.status != "pending_confirmation"
        or proposal.version != request.expected_version
    ):
        return proposal_business_error_result(
            "proposal_execution_conflict"
        )
    proposal.status = "failed" if code == "proposal_execution_failed" else "stale"
    proposal.version += 1
    proposal.decision_action = "confirm"
    proposal.decision_client_request_id = request.client_request_id
    proposal.confirmed_at = now
    proposal.last_error_code = code
    await db.flush()
    return proposal_business_error_result(code, state_changed=True)


async def apply_confirmed_plan_adjustment_atomically(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    proposal_id: str,
    request: PlanAdjustmentProposalDecisionRequest,
    now: datetime,
    fault_injector: PlanAdjustmentExecutionFaultInjector | None = None,
) -> PlanAdjustmentProposalDecisionServiceResult:
    """Flush one atomic apply result; the API caller owns the outer commit."""

    from app.services.training_lifecycle import lock_training_user
    await lock_training_user(db, user_id)

    decision = await decide_plan_adjustment_proposal(
        db,
        enabled=enabled,
        user_id=user_id,
        proposal_id=proposal_id,
        action="confirm",
        request=request,
        now=now,
    )
    if decision.outcome != "ready_to_apply":
        return decision
    confirmation = decision.confirmation
    if confirmation is None:  # pragma: no cover - result invariant
        raise ValueError("ready confirmation is missing")

    try:
        async with db.begin_nested():
            payload = confirmation.payload
            candidate = _validated_first_cohort_candidate(payload)
            base_plan = await _locked_active_plan(
                db,
                user_id=user_id,
                expected_plan_id=payload.target.base_plan_id,
            )
            current, exercises = await _locked_plan_snapshot(
                db,
                plan=base_plan,
            )
            if (
                current != payload.before
                or plan_adjustment_plan_snapshot_fingerprint(current)
                != payload.target.base_plan_fingerprint
            ):
                _reject("proposal_base_plan_changed")
            await _validate_latest_health_context(
                db,
                user_id=user_id,
                exercises=exercises,
            )
            new_plan = await _persist_candidate_plan(
                db,
                user_id=user_id,
                base_plan=base_plan,
                candidate=candidate,
            )
            _inject_fault(fault_injector, "candidate_persisted")

            base_plan.is_active = False
            new_plan.is_active = True
            await db.flush()
            _inject_fault(fault_injector, "active_plan_switched")

            proposal: AgentProposal = confirmation.proposal
            proposal.status = "applied"
            proposal.version += 1
            proposal.decision_action = "confirm"
            proposal.decision_client_request_id = request.client_request_id
            proposal.confirmed_at = now
            proposal.applied_at = now
            proposal.result_plan_id = new_plan.id
            proposal.result_plan_fingerprint = (
                plan_adjustment_plan_snapshot_fingerprint(candidate)
            )
            proposal.last_error_code = None
            await db.flush()
            _inject_fault(fault_injector, "proposal_applied")
            response = build_plan_adjustment_proposal_decision_response(
                proposal
            )
    except _PlanAdjustmentExecutionRejected as exc:
        return await _record_confirmation_error(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            request=request,
            now=now,
            code=exc.code,
        )
    except Exception:
        return await _record_confirmation_error(
            db,
            user_id=user_id,
            proposal_id=proposal_id,
            request=request,
            now=now,
            code="proposal_execution_failed",
        )
    return PlanAdjustmentProposalDecisionServiceResult(
        outcome="completed",
        response=response,
        state_changed=True,
    )
