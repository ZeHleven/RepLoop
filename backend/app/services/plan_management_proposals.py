from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal

from pydantic import ValidationError
from sqlalchemy import delete, select, update, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AgentProposal
from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.workout import PlannedExercise, WorkoutPlan, WorkoutSession
from app.schemas.plan_management_proposal import (
    CreatePlanAdjustmentProposalRequest,
    CreatePlanDeletionProposalRequest,
    GenericProposalDecisionRequest,
    GenericProposalDecisionResponse,
    GenericProposalReadResponse,
    PlanAdjustmentPayloadV2,
    PlanCandidate,
    PlanChangeV2,
    PlanDeletionPayloadV1,
    PlanEditContext,
    PlanExerciseSnapshotV2,
    PlanProposalReference,
    PlanProposalTarget,
    PlanSnapshotV2,
)
from app.services.personalized_planner import is_exercise_compatible
from app.services.custom_exercises import visible_exercise, safety_notice
from app.services.training_lifecycle import lock_training_user
from app.services.workout_queries import get_active_user_session
from app.services.exercise_search import selection_metadata

if TYPE_CHECKING:
    from app.services.adaptive_planner import AdaptiveAdjustmentProposal


PLAN_MANAGEMENT_TYPES = ("plan_adjustment_v2", "plan_deletion_v1")


class PlanProposalError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def plan_snapshot_fingerprint(snapshot: PlanSnapshotV2) -> str:
    semantic = snapshot.model_dump(mode="json")
    for item in semantic["exercises"]:
        # Planned-exercise row ids are stable inside one proposal but change when
        # the immutable successor plan is persisted. They are not plan semantics.
        item.pop("item_key", None)
    return _fingerprint(semantic)


def health_context_fingerprint(profile: UserProfile) -> str:
    return _fingerprint({
        "onboarding_completed": profile.onboarding_completed,
        "experience_level": profile.experience_level,
        "training_location": profile.training_location,
        "injuries": sorted(str(value) for value in (profile.injuries or [])),
        "chronic_conditions": sorted(
            str(value) for value in (profile.chronic_conditions or [])
        ),
    })


async def _profile_for_update(
    db: AsyncSession,
    *,
    user_id: str,
    lock: bool = False,
) -> UserProfile:
    query = select(UserProfile).where(UserProfile.user_id == user_id)
    if lock:
        query = query.with_for_update()
    profile = await db.scalar(query)
    if profile is None or not profile.onboarding_completed:
        raise PlanProposalError(
            "profile_incomplete",
            "请先完善个人档案和健康筛查",
            status_code=422,
        )
    return profile


async def _owned_plan(
    db: AsyncSession,
    *,
    user_id: str,
    plan_id: str,
    active_required: bool = True,
    lock: bool = False,
) -> WorkoutPlan:
    query = select(WorkoutPlan).where(
        WorkoutPlan.id == plan_id,
        WorkoutPlan.user_id == user_id,
    )
    if active_required:
        query = query.where(WorkoutPlan.is_active.is_(True))
    if lock:
        query = query.with_for_update()
    plan = await db.scalar(query)
    if plan is None:
        raise PlanProposalError(
            "proposal_base_plan_changed",
            "当前活动计划已变化，请刷新后重新编辑",
            status_code=409,
        )
    return plan


async def _lock_exact_active_plan(
    db: AsyncSession,
    *,
    user_id: str,
    expected_plan_id: str,
) -> None:
    active_ids = list((await db.execute(
        select(WorkoutPlan.id)
        .where(
            WorkoutPlan.user_id == user_id,
            WorkoutPlan.is_active.is_(True),
        )
        .order_by(WorkoutPlan.id)
        .with_for_update()
    )).scalars().all())
    if active_ids != [expected_plan_id]:
        raise PlanProposalError(
            "proposal_base_plan_changed",
            "活动计划集合已变化，请刷新后重新操作",
        )


async def build_plan_snapshot_v2(
    db: AsyncSession,
    *,
    plan: WorkoutPlan,
    lock: bool = False,
) -> PlanSnapshotV2:
    query = (
        select(PlannedExercise)
        .where(PlannedExercise.plan_id == plan.id)
        .order_by(PlannedExercise.day_of_week, PlannedExercise.order_index)
    )
    if lock:
        query = query.with_for_update()
    planned = list((await db.execute(query)).scalars().all())
    if not planned:
        raise PlanProposalError(
            "proposal_candidate_unavailable",
            "当前计划没有可编辑的训练动作",
            status_code=422,
        )

    exercise_ids = {item.exercise_id for item in planned}
    exercise_query = select(Exercise).where(Exercise.id.in_(exercise_ids))
    if lock:
        exercise_query = exercise_query.with_for_update()
    exercises = list((await db.execute(exercise_query)).scalars().all())
    by_id = {item.id: item for item in exercises}
    if len(by_id) != len(exercise_ids):
        raise PlanProposalError(
            "proposal_candidate_unavailable",
            "当前计划包含不存在的动作",
            status_code=422,
        )
    try:
        items = [
            PlanExerciseSnapshotV2(
                item_key=f"planned:{item.id}",
                exercise_id=item.exercise_id,
                exercise_name=by_id[item.exercise_id].name_zh,
                category=by_id[item.exercise_id].category,
                day_of_week=item.day_of_week,
                sets=item.sets,
                reps=item.reps,
                rest_seconds=item.rest_seconds,
                recommended_weight_kg=item.recommended_weight_kg,
                order_index=item.order_index,
            )
            for item in planned
        ]
        training_days = sorted({item.day_of_week for item in planned})
        return PlanSnapshotV2(
            name=plan.name,
            goal=plan.goal,
            duration_weeks=plan.duration_weeks,
            days_per_week=len(training_days),
            training_days=training_days,
            exercises=items,
        )
    except ValidationError as exc:
        raise PlanProposalError(
            "proposal_payload_invalid",
            "当前计划数据不符合编辑器约束",
            status_code=422,
        ) from exc


async def build_plan_edit_context(
    db: AsyncSession,
    *,
    user_id: str,
    plan_id: str,
    proposals_enabled: bool,
) -> PlanEditContext:
    plan = await _owned_plan(db, user_id=user_id, plan_id=plan_id)
    profile = await _profile_for_update(db, user_id=user_id)
    snapshot = await build_plan_snapshot_v2(db, plan=plan)
    options = list((await db.execute(
        select(Exercise)
        .where(Exercise.is_active.is_(True), visible_exercise(user_id))
        .order_by(Exercise.name_zh)
        .limit(300)
    )).scalars().all())
    compatible = [item for item in options if is_exercise_compatible(profile, item)]
    private_used = (await db.execute(select(Exercise).where(
        Exercise.id.in_({item.exercise_id for item in snapshot.exercises}),
        Exercise.owner_id == user_id,
    ))).scalars().all()
    active_session = await get_active_user_session(db, user_id=user_id)
    return PlanEditContext(
        base_plan=snapshot,
        base_plan_fingerprint=plan_snapshot_fingerprint(snapshot),
        health_context_fingerprint=health_context_fingerprint(profile),
        exercise_options=[{
            **selection_metadata(item),
            "exercise_id": item.id,
            "exercise_name": item.name_zh,
            "category": item.category,
            "difficulty": item.difficulty,
            "equipment": list(item.equipment or []),
            "safety_notice": safety_notice(item),
        } for item in compatible],
        exercise_notices={item.id: safety_notice(item) for item in private_used},
        active_session=active_session is not None,
        proposals_enabled=proposals_enabled,
    )


def _validate_candidate_structure(
    *,
    before: PlanSnapshotV2,
    candidate: PlanCandidate,
) -> None:
    keys = [item.item_key for item in candidate.exercises]
    if len(keys) != len(set(keys)):
        raise PlanProposalError("proposal_payload_invalid", "动作标识不能重复", status_code=422)
    before_keys = {item.item_key for item in before.exercises}
    for key in keys:
        if key.startswith("planned:") and key not in before_keys:
            raise PlanProposalError(
                "proposal_payload_invalid", "计划包含未知的原动作标识", status_code=422
            )
    used_days = {item.day_of_week for item in candidate.exercises}
    if used_days != set(candidate.training_days):
        raise PlanProposalError(
            "proposal_payload_invalid",
            "每个训练日都必须包含动作，且动作不能落在未选择的日期",
            status_code=422,
        )
    duplicates = [
        (item.day_of_week, item.exercise_id) for item in candidate.exercises
    ]
    if len(duplicates) != len(set(duplicates)):
        raise PlanProposalError(
            "proposal_payload_invalid", "同一训练日不能重复安排相同动作", status_code=422
        )
    for day in candidate.training_days:
        orders = sorted(
            item.order_index for item in candidate.exercises if item.day_of_week == day
        )
        if orders != list(range(len(orders))):
            raise PlanProposalError(
                "proposal_payload_invalid", "每个训练日的动作顺序必须连续", status_code=422
            )


async def _hydrate_candidate(
    db: AsyncSession,
    *,
    before: PlanSnapshotV2,
    candidate: PlanCandidate,
    profile: UserProfile,
    lock: bool = False,
) -> tuple[PlanSnapshotV2, list[Exercise]]:
    _validate_candidate_structure(before=before, candidate=candidate)
    exercise_ids = {item.exercise_id for item in candidate.exercises}
    query = select(Exercise).where(Exercise.id.in_(exercise_ids), visible_exercise(profile.user_id))
    if lock:
        query = query.with_for_update()
    exercises = list((await db.execute(query)).scalars().all())
    by_id = {item.id: item for item in exercises}
    if len(by_id) != len(exercise_ids) or any(not item.is_active for item in exercises):
        raise PlanProposalError(
            "proposal_candidate_unavailable", "候选计划包含不存在或已停用的动作", status_code=422
        )
    incompatible = [
        item.name_zh for item in exercises if not is_exercise_compatible(profile, item)
    ]
    if incompatible:
        raise PlanProposalError(
            "proposal_health_context_changed",
            f"以下动作不符合当前健康或训练条件：{'、'.join(sorted(incompatible))}",
            status_code=409,
        )
    items = [
        PlanExerciseSnapshotV2(
            **item.model_dump(),
            exercise_name=by_id[item.exercise_id].name_zh,
            category=by_id[item.exercise_id].category,
        )
        for item in sorted(
            candidate.exercises,
            key=lambda value: (value.day_of_week, value.order_index, value.item_key),
        )
    ]
    return PlanSnapshotV2(
        name=before.name,
        goal=before.goal,
        duration_weeks=candidate.duration_weeks,
        days_per_week=len(candidate.training_days),
        training_days=candidate.training_days,
        exercises=items,
    ), exercises


def compile_plan_changes(
    before: PlanSnapshotV2,
    after: PlanSnapshotV2,
) -> list[PlanChangeV2]:
    changes: list[PlanChangeV2] = []
    if (
        before.duration_weeks != after.duration_weeks
        or before.training_days != after.training_days
    ):
        changes.append(PlanChangeV2(
            change_type="update_schedule",
            stable_display_key="schedule",
            before={
                "duration_weeks": before.duration_weeks,
                "training_days": before.training_days,
                "days_per_week": before.days_per_week,
            },
            after={
                "duration_weeks": after.duration_weeks,
                "training_days": after.training_days,
                "days_per_week": after.days_per_week,
            },
            reason="按你保存的草稿调整计划周期和每周训练日",
        ))

    before_by_key = {item.item_key: item for item in before.exercises}
    after_by_key = {item.item_key: item for item in after.exercises}
    for key in sorted(before_by_key.keys() - after_by_key.keys()):
        changes.append(PlanChangeV2(
            change_type="remove_exercise",
            stable_display_key=key,
            before=before_by_key[key].model_dump(mode="json"),
            reason="从计划中移除该动作",
        ))
    for key in sorted(after_by_key.keys() - before_by_key.keys()):
        changes.append(PlanChangeV2(
            change_type="add_exercise",
            stable_display_key=key,
            after=after_by_key[key].model_dump(mode="json"),
            reason="向计划中加入该动作",
        ))
    for key in sorted(before_by_key.keys() & after_by_key.keys()):
        old = before_by_key[key]
        new = after_by_key[key]
        if old.exercise_id != new.exercise_id:
            changes.append(PlanChangeV2(
                change_type="replace_exercise",
                stable_display_key=key,
                before={"exercise_id": old.exercise_id, "exercise_name": old.exercise_name},
                after={"exercise_id": new.exercise_id, "exercise_name": new.exercise_name},
                reason="替换该位置的训练动作",
            ))
        if (old.day_of_week, old.order_index) != (new.day_of_week, new.order_index):
            changes.append(PlanChangeV2(
                change_type="move_exercise",
                stable_display_key=key,
                before={"day_of_week": old.day_of_week, "order_index": old.order_index},
                after={"day_of_week": new.day_of_week, "order_index": new.order_index},
                reason="调整动作所在训练日或日内顺序",
            ))
        before_targets = {
            "sets": old.sets,
            "reps": old.reps,
            "rest_seconds": old.rest_seconds,
            "recommended_weight_kg": old.recommended_weight_kg,
        }
        after_targets = {
            "sets": new.sets,
            "reps": new.reps,
            "rest_seconds": new.rest_seconds,
            "recommended_weight_kg": new.recommended_weight_kg,
        }
        if before_targets != after_targets:
            changes.append(PlanChangeV2(
                change_type="adjust_exercise_target",
                stable_display_key=key,
                before=before_targets,
                after=after_targets,
                reason="按你填写的数值调整动作目标",
            ))
    return changes


def _change_effects(changes: list[PlanChangeV2]) -> list[dict[str, Any]]:
    """Compare executable change semantics independently of display copy."""
    return [
        {
            key: value
            for key, value in change.model_dump(mode="json").items()
            if key not in {"reason", "safety_priority"}
        }
        for change in changes
    ]


def _proposal_reference(proposal: AgentProposal) -> PlanProposalReference:
    if proposal.expires_at is None or proposal.payload_fingerprint is None:
        raise ValueError("proposal lifecycle metadata is incomplete")
    return PlanProposalReference(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=proposal.status,
        version=proposal.version,
        expires_at=proposal.expires_at,
        payload_fingerprint=proposal.payload_fingerprint,
    )


async def _idempotent_creation(
    db: AsyncSession,
    *,
    user_id: str,
    plan_id: str,
    proposal_type: str,
    client_request_id: str,
) -> PlanProposalReference | None:
    existing = await db.scalar(select(AgentProposal).where(
        AgentProposal.user_id == user_id,
        AgentProposal.creation_client_request_id == client_request_id,
    ))
    if existing is None:
        return None
    if existing.target_id != plan_id or existing.proposal_type != proposal_type:
        raise PlanProposalError(
            "proposal_idempotency_conflict",
            "该请求标识已用于另一份提案",
        )
    return _proposal_reference(existing)


async def _commit_proposal_creation(
    db: AsyncSession,
    *,
    proposal: AgentProposal,
    user_id: str,
    plan_id: str,
    proposal_type: str,
    client_request_id: str,
) -> PlanProposalReference:
    try:
        await db.commit()
        await db.refresh(proposal)
        return _proposal_reference(proposal)
    except IntegrityError as exc:
        # Concurrent retries can both pass the initial read.  The database
        # unique constraint is authoritative; after rollback, project the
        # winner as the idempotent response instead of surfacing a 500.
        await db.rollback()
        replay = await _idempotent_creation(
            db,
            user_id=user_id,
            plan_id=plan_id,
            proposal_type=proposal_type,
            client_request_id=client_request_id,
        )
        if replay is not None:
            return replay
        raise PlanProposalError(
            "proposal_creation_conflict",
            "提案创建发生并发冲突，请刷新后重试",
        ) from exc


async def _supersede_pending_plan_proposals(
    db: AsyncSession,
    *,
    user_id: str,
    plan_id: str,
) -> None:
    pending = list((await db.execute(
        select(AgentProposal)
        .where(
            AgentProposal.user_id == user_id,
            AgentProposal.base_plan_id == plan_id,
            AgentProposal.proposal_type.in_((
                "plan_adjustment_v1", "plan_adjustment_v2", "plan_deletion_v1"
            )),
            AgentProposal.status == "pending_confirmation",
        )
        .with_for_update()
    )).scalars().all())
    for proposal in pending:
        proposal.status = "stale"
        proposal.version += 1
        proposal.last_error_code = "proposal_superseded"


async def create_workout_completion_adjustment_proposal(
    db: AsyncSession,
    *,
    session: WorkoutSession,
    proposals: list["AdaptiveAdjustmentProposal"],
    now: datetime | None = None,
) -> tuple[PlanProposalReference | None, str, list[dict[str, Any]]]:
    """Persist safe adaptive suggestions without applying them to the plan."""
    effective = [item for item in proposals if item.before != item.after]
    if not effective or session.plan_id is None:
        return None, "not_needed", []

    pending = list((await db.execute(
        select(AgentProposal)
        .where(
            AgentProposal.user_id == session.user_id,
            AgentProposal.base_plan_id == session.plan_id,
            AgentProposal.proposal_type.in_((
                "plan_adjustment_v1", "plan_adjustment_v2", "plan_deletion_v1"
            )),
            AgentProposal.status == "pending_confirmation",
        )
        .with_for_update()
    )).scalars().all())
    explicit = [item for item in pending if item.origin != "workout_completion"]
    if explicit:
        return None, "blocked_by_existing", []

    plan = await _owned_plan(
        db,
        user_id=session.user_id,
        plan_id=session.plan_id,
        lock=True,
    )
    profile = await _profile_for_update(db, user_id=session.user_id, lock=True)
    before = await build_plan_snapshot_v2(db, plan=plan, lock=True)
    before_by_key = {item.item_key: item for item in before.exercises}
    proposal_by_key = {
        f"planned:{item.planned_exercise_id}": item for item in effective
    }
    if not set(proposal_by_key).issubset(before_by_key):
        raise PlanProposalError(
            "proposal_base_plan_changed",
            "训练完成后活动计划已变化，未生成调整提案",
        )

    after_items: list[PlanExerciseSnapshotV2] = []
    for item in before.exercises:
        adaptive = proposal_by_key.get(item.item_key)
        if adaptive is None:
            after_items.append(item)
            continue
        after_items.append(item.model_copy(update={
            "exercise_id": adaptive.after["exercise_id"],
            "exercise_name": adaptive.after["exercise_name"],
            "sets": adaptive.after["sets"],
            "reps": adaptive.after["reps"],
            "rest_seconds": adaptive.after["rest_seconds"],
            "recommended_weight_kg": adaptive.after["recommended_weight_kg"],
        }))
    candidate = PlanCandidate(
        duration_weeks=before.duration_weeks,
        training_days=before.training_days,
        exercises=[{
            key: value
            for key, value in item.model_dump().items()
            if key not in {"exercise_name", "category"}
        } for item in after_items],
    )
    after, _ = await _hydrate_candidate(
        db,
        before=before,
        candidate=candidate,
        profile=profile,
        lock=True,
    )
    changes = compile_plan_changes(before, after)
    if not changes:
        return None, "not_needed", []
    for change in changes:
        adaptive = proposal_by_key.get(change.stable_display_key)
        if adaptive is not None:
            change.reason = adaptive.reason
            change.safety_priority = adaptive.safety_priority

    for old in pending:
        old.status = "stale"
        old.version += 1
        old.last_error_code = "proposal_superseded_by_workout"

    target = PlanProposalTarget(
        base_plan_id=plan.id,
        base_plan_fingerprint=plan_snapshot_fingerprint(before),
        health_context_fingerprint=health_context_fingerprint(profile),
    )
    payload = PlanAdjustmentPayloadV2(
        target=target,
        before=before,
        after=after,
        changes=changes,
        rationale=["根据本次训练完成度和你提交的主观反馈生成。"],
        safety_notes=[
            "当前仅保存为待确认建议；确认前不会修改活动计划。",
            "确认时会重新检查计划版本、动作可用性和最新健康资料。",
        ],
    )
    moment = now or datetime.now(timezone.utc)
    payload_data = payload.model_dump(mode="json")
    proposal = AgentProposal(
        user_id=session.user_id,
        conversation_id=None,
        run_id=None,
        proposal_type="plan_adjustment_v2",
        origin="workout_completion",
        creation_client_request_id=f"workout-completion:{session.id}:adaptive-v1",
        target_kind="workout_plan",
        target_id=plan.id,
        payload_data=payload_data,
        payload_fingerprint=_fingerprint(payload_data),
        base_plan_id=plan.id,
        base_plan_fingerprint=target.base_plan_fingerprint,
        status="pending_confirmation",
        expires_at=moment + timedelta(hours=48),
    )
    db.add(proposal)
    await db.flush()
    return (
        _proposal_reference(proposal),
        "pending_confirmation",
        [item.to_response() for item in effective],
    )


async def create_manual_plan_adjustment_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    plan_id: str,
    request: CreatePlanAdjustmentProposalRequest,
    now: datetime | None = None,
    origin: Literal["agent_chat", "manual_editor", "workout_completion"] = "manual_editor",
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "手动计划编辑功能尚未启用", status_code=403
        )
    await lock_training_user(db, user_id)
    replay = await _idempotent_creation(
        db,
        user_id=user_id,
        plan_id=plan_id,
        proposal_type="plan_adjustment_v2",
        client_request_id=request.client_request_id,
    )
    if replay is not None:
        return replay

    # Serialize drafts targeting the same active plan so the later proposal can
    # deterministically supersede every earlier pending proposal.
    plan = await _owned_plan(db, user_id=user_id, plan_id=plan_id, lock=True)
    profile = await _profile_for_update(db, user_id=user_id)
    before = await build_plan_snapshot_v2(db, plan=plan, lock=True)
    base_fingerprint = plan_snapshot_fingerprint(before)
    if base_fingerprint != request.expected_base_fingerprint:
        raise PlanProposalError(
            "proposal_base_plan_changed", "计划已变化，请刷新编辑器后重试"
        )
    after, _ = await _hydrate_candidate(
        db,
        before=before,
        candidate=request.candidate,
        profile=profile,
    )
    changes = compile_plan_changes(before, after)
    if not changes:
        raise PlanProposalError(
            "proposal_no_change", "计划没有发生变化", status_code=422
        )
    target = PlanProposalTarget(
        base_plan_id=plan.id,
        base_plan_fingerprint=base_fingerprint,
        health_context_fingerprint=health_context_fingerprint(profile),
    )
    payload = PlanAdjustmentPayloadV2(
        target=target,
        before=before,
        after=after,
        changes=changes,
        rationale=["本提案完全根据你在计划编辑器中保存的草稿生成。"],
        safety_notes=["确认时会重新检查活动计划版本、动作可用性和最新健康资料。"],
    )
    moment = now or datetime.now(timezone.utc)
    await _supersede_pending_plan_proposals(db, user_id=user_id, plan_id=plan.id)
    payload_data = payload.model_dump(mode="json")
    proposal = AgentProposal(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type="plan_adjustment_v2",
        origin=origin,
        creation_client_request_id=request.client_request_id,
        target_kind="workout_plan",
        target_id=plan.id,
        payload_data=payload_data,
        payload_fingerprint=_fingerprint(payload_data),
        base_plan_id=plan.id,
        base_plan_fingerprint=base_fingerprint,
        status="pending_confirmation",
        expires_at=moment + timedelta(hours=48),
    )
    db.add(proposal)
    return await _commit_proposal_creation(
        db,
        proposal=proposal,
        user_id=user_id,
        plan_id=plan.id,
        proposal_type="plan_adjustment_v2",
        client_request_id=request.client_request_id,
    )


async def create_manual_plan_deletion_proposal(
    db: AsyncSession,
    *,
    enabled: bool,
    user_id: str,
    plan_id: str,
    request: CreatePlanDeletionProposalRequest,
    now: datetime | None = None,
    origin: Literal["agent_chat", "manual_editor"] = "manual_editor",
    conversation_id: str | None = None,
    run_id: str | None = None,
) -> PlanProposalReference:
    if not enabled:
        raise PlanProposalError(
            "proposal_feature_disabled", "计划删除功能尚未启用", status_code=403
        )
    await lock_training_user(db, user_id)
    replay = await _idempotent_creation(
        db,
        user_id=user_id,
        plan_id=plan_id,
        proposal_type="plan_deletion_v1",
        client_request_id=request.client_request_id,
    )
    if replay is not None:
        return replay
    plan = await _owned_plan(db, user_id=user_id, plan_id=plan_id, lock=True)
    profile = await _profile_for_update(db, user_id=user_id)
    before = await build_plan_snapshot_v2(db, plan=plan, lock=True)
    base_fingerprint = plan_snapshot_fingerprint(before)
    if base_fingerprint != request.expected_base_fingerprint:
        raise PlanProposalError(
            "proposal_base_plan_changed", "计划已变化，请刷新后重试"
        )
    target = PlanProposalTarget(
        base_plan_id=plan.id,
        base_plan_fingerprint=base_fingerprint,
        health_context_fingerprint=health_context_fingerprint(profile),
    )
    payload = PlanDeletionPayloadV1(
        target=target,
        before=before,
        consequences=[
            "永久删除当前活动计划及其中的动作编排。",
            "保留所有历史训练记录、逐组数据与计划名称。",
            "删除后需要重新生成计划才能开始下一次计划训练。",
        ],
        safety_notes=["如有进行中的训练，请先完成或保留记录并结束；系统不会替你删除已记录组数。"],
    )
    moment = now or datetime.now(timezone.utc)
    await _supersede_pending_plan_proposals(db, user_id=user_id, plan_id=plan.id)
    payload_data = payload.model_dump(mode="json")
    proposal = AgentProposal(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        proposal_type="plan_deletion_v1",
        origin=origin,
        creation_client_request_id=request.client_request_id,
        target_kind="workout_plan",
        target_id=plan.id,
        payload_data=payload_data,
        payload_fingerprint=_fingerprint(payload_data),
        base_plan_id=plan.id,
        base_plan_fingerprint=base_fingerprint,
        status="pending_confirmation",
        expires_at=moment + timedelta(hours=48),
    )
    db.add(proposal)
    return await _commit_proposal_creation(
        db,
        proposal=proposal,
        user_id=user_id,
        plan_id=plan.id,
        proposal_type="plan_deletion_v1",
        client_request_id=request.client_request_id,
    )


def _parse_manual_payload(
    proposal: AgentProposal,
) -> PlanAdjustmentPayloadV2 | PlanDeletionPayloadV1:
    try:
        if proposal.proposal_type == "plan_adjustment_v2":
            return PlanAdjustmentPayloadV2.model_validate(proposal.payload_data)
        if proposal.proposal_type == "plan_deletion_v1":
            return PlanDeletionPayloadV1.model_validate(proposal.payload_data)
    except ValidationError as exc:
        raise PlanProposalError("proposal_payload_invalid", "提案内容已损坏") from exc
    raise PlanProposalError("proposal_not_found", "提案不存在", status_code=404)


def project_manual_proposal_read(
    proposal: AgentProposal,
    *,
    now: datetime,
) -> GenericProposalReadResponse:
    if proposal.payload_fingerprint is None or proposal.expires_at is None:
        raise PlanProposalError("proposal_payload_invalid", "提案生命周期数据不完整")
    payload = _parse_manual_payload(proposal)
    status = proposal.status
    if status == "pending_confirmation" and now >= proposal.expires_at:
        status = "expired"
    result: dict[str, Any] | None = None
    if status == "applied":
        if proposal.proposal_type == "plan_adjustment_v2":
            result = {
                "plan_id": proposal.result_plan_id,
                "plan_fingerprint": proposal.result_plan_fingerprint,
                "applied_at": proposal.applied_at,
            }
        else:
            result = proposal.result_data
    return GenericProposalReadResponse(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        origin=proposal.origin,
        status=status,
        version=proposal.version,
        payload_fingerprint=proposal.payload_fingerprint,
        payload=payload.model_dump(mode="json"),
        expires_at=proposal.expires_at,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
        allowed_actions=["confirm", "reject"] if status == "pending_confirmation" else [],
        result=result,
    )


async def read_owned_manual_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    now: datetime | None = None,
) -> GenericProposalReadResponse | None:
    proposal = await db.scalar(select(AgentProposal).where(
        AgentProposal.id == proposal_id,
        AgentProposal.user_id == user_id,
        AgentProposal.proposal_type.in_(PLAN_MANAGEMENT_TYPES),
    ))
    if proposal is None:
        return None
    return project_manual_proposal_read(
        proposal,
        now=now or datetime.now(timezone.utc),
    )


async def _decision_request_conflict(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    client_request_id: str,
) -> bool:
    existing = await db.scalar(select(AgentProposal.id).where(
        AgentProposal.user_id == user_id,
        AgentProposal.decision_client_request_id == client_request_id,
    ))
    return existing is not None and existing != proposal_id


def _decision_response(proposal: AgentProposal) -> GenericProposalDecisionResponse:
    decided_at = proposal.applied_at if proposal.status == "applied" else proposal.rejected_at
    if decided_at is None or proposal.payload_fingerprint is None:
        raise ValueError("decided proposal metadata is incomplete")
    return GenericProposalDecisionResponse(
        id=proposal.id,
        proposal_type=proposal.proposal_type,
        status=proposal.status,
        version=proposal.version,
        applied=proposal.status == "applied",
        payload_fingerprint=proposal.payload_fingerprint,
        result_plan_id=proposal.result_plan_id,
        result_plan_fingerprint=proposal.result_plan_fingerprint,
        result_data=proposal.result_data,
        decided_at=decided_at,
    )


async def _apply_adjustment_v2(
    db: AsyncSession,
    *,
    proposal: AgentProposal,
    payload: PlanAdjustmentPayloadV2,
    user_id: str,
) -> tuple[str, str]:
    plan = await _owned_plan(
        db, user_id=user_id, plan_id=payload.target.base_plan_id, lock=True
    )
    await _lock_exact_active_plan(
        db, user_id=user_id, expected_plan_id=plan.id
    )
    current = await build_plan_snapshot_v2(db, plan=plan, lock=True)
    if (
        current != payload.before
        or plan_snapshot_fingerprint(current) != payload.target.base_plan_fingerprint
    ):
        raise PlanProposalError(
            "proposal_base_plan_changed", "活动计划已变化，提案不能继续执行"
        )
    profile = await _profile_for_update(db, user_id=user_id, lock=True)
    if health_context_fingerprint(profile) != payload.target.health_context_fingerprint:
        raise PlanProposalError(
            "proposal_health_context_changed", "健康资料已变化，请重新检查计划"
        )
    candidate = PlanCandidate(
        duration_weeks=payload.after.duration_weeks,
        training_days=payload.after.training_days,
        exercises=[{
            key: value
            for key, value in item.model_dump().items()
            if key not in {"exercise_name", "category"}
        } for item in payload.after.exercises],
    )
    hydrated, _ = await _hydrate_candidate(
        db,
        before=current,
        candidate=candidate,
        profile=profile,
        lock=True,
    )
    if (
        hydrated != payload.after
        or _change_effects(compile_plan_changes(current, hydrated))
        != _change_effects(payload.changes)
    ):
        raise PlanProposalError("proposal_payload_invalid", "提案差异校验失败")

    new_plan = WorkoutPlan(
        user_id=user_id,
        family_id=plan.family_id,
        name=hydrated.name,
        goal=hydrated.goal,
        duration_weeks=hydrated.duration_weeks,
        days_per_week=hydrated.days_per_week,
        is_active=False,
        ai_generated=plan.ai_generated,
        notes=plan.notes,
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
        for item in hydrated.exercises
    ])
    plan.is_active = False
    new_plan.is_active = True
    await db.flush()
    return new_plan.id, plan_snapshot_fingerprint(hydrated)


async def _apply_plan_deletion(
    db: AsyncSession,
    *,
    payload: PlanDeletionPayloadV1,
    user_id: str,
) -> dict[str, Any]:
    plan = await _owned_plan(
        db, user_id=user_id, plan_id=payload.target.base_plan_id, lock=True
    )
    await _lock_exact_active_plan(
        db, user_id=user_id, expected_plan_id=plan.id
    )
    current = await build_plan_snapshot_v2(db, plan=plan, lock=True)
    if (
        current != payload.before
        or plan_snapshot_fingerprint(current) != payload.target.base_plan_fingerprint
    ):
        raise PlanProposalError(
            "proposal_base_plan_changed", "活动计划已变化，删除提案不能继续执行"
        )
    profile = await _profile_for_update(db, user_id=user_id, lock=True)
    if health_context_fingerprint(profile) != payload.target.health_context_fingerprint:
        raise PlanProposalError(
            "proposal_health_context_changed", "健康资料已变化，请重新检查删除操作"
        )
    active = await db.scalar(select(WorkoutSession.id).where(
        WorkoutSession.user_id == user_id, WorkoutSession.status == 'in_progress',
        or_(WorkoutSession.plan_family_id == plan.family_id, WorkoutSession.plan_id == plan.id),
    ).limit(1))
    if active is not None:
        raise PlanProposalError('workout_in_progress', '请先完成训练，或选择“保留记录并结束”，再删除计划')
    await db.execute(
        update(WorkoutSession)
        .where(WorkoutSession.plan_id == plan.id)
        .values(plan_id=None, plan_name=plan.name)
    )
    await db.execute(delete(PlannedExercise).where(PlannedExercise.plan_id == plan.id))
    deleted_id = plan.id
    await db.delete(plan)
    await db.flush()
    return {
        "deleted_plan_id": deleted_id,
        "history_preserved": True,
        "active_plan_exists": False,
    }


async def decide_manual_plan_proposal(
    db: AsyncSession,
    *,
    user_id: str,
    proposal_id: str,
    action: Literal["confirm", "reject"],
    request: GenericProposalDecisionRequest,
    now: datetime | None = None,
) -> GenericProposalDecisionResponse:
    await lock_training_user(db, user_id)
    moment = now or datetime.now(timezone.utc)
    proposal = await db.scalar(
        select(AgentProposal)
        .where(
            AgentProposal.id == proposal_id,
            AgentProposal.user_id == user_id,
            AgentProposal.proposal_type.in_(PLAN_MANAGEMENT_TYPES),
        )
        .with_for_update()
    )
    if proposal is None:
        raise PlanProposalError("proposal_not_found", "提案不存在", status_code=404)
    if await _decision_request_conflict(
        db,
        user_id=user_id,
        proposal_id=proposal.id,
        client_request_id=request.client_request_id,
    ):
        raise PlanProposalError("proposal_idempotency_conflict", "决策请求标识已被占用")
    if (
        proposal.status == ("applied" if action == "confirm" else "rejected")
        and proposal.decision_action == action
    ):
        return _decision_response(proposal)
    if proposal.status != "pending_confirmation":
        raise PlanProposalError("proposal_not_pending", "提案已不能继续决策")
    if proposal.version != request.expected_version:
        raise PlanProposalError("proposal_version_conflict", "提案版本已变化，请刷新")
    if proposal.expires_at is None or moment >= proposal.expires_at:
        proposal.status = "expired"
        proposal.version += 1
        proposal.last_error_code = "proposal_expired"
        await db.commit()
        raise PlanProposalError("proposal_expired", "提案已过期")

    if action == "reject":
        proposal.status = "rejected"
        proposal.version += 1
        proposal.decision_action = "reject"
        proposal.decision_client_request_id = request.client_request_id
        proposal.rejected_at = moment
        proposal.last_error_code = None
        await db.commit()
        await db.refresh(proposal)
        return _decision_response(proposal)

    try:
        payload = _parse_manual_payload(proposal)
        async with db.begin_nested():
            if isinstance(payload, PlanAdjustmentPayloadV2):
                plan_id, plan_fingerprint = await _apply_adjustment_v2(
                    db,
                    proposal=proposal,
                    payload=payload,
                    user_id=user_id,
                )
                proposal.result_plan_id = plan_id
                proposal.result_plan_fingerprint = plan_fingerprint
            else:
                proposal.result_data = await _apply_plan_deletion(
                    db, payload=payload, user_id=user_id
                )
            proposal.status = "applied"
            proposal.version += 1
            proposal.decision_action = "confirm"
            proposal.decision_client_request_id = request.client_request_id
            proposal.confirmed_at = moment
            proposal.applied_at = moment
            proposal.last_error_code = None
            await db.flush()
        await db.commit()
        await db.refresh(proposal)
        return _decision_response(proposal)
    except PlanProposalError as exc:
        await db.rollback()
        failed = await db.scalar(
            select(AgentProposal)
            .where(
                AgentProposal.id == proposal_id,
                AgentProposal.user_id == user_id,
            )
            .with_for_update()
        )
        if (
            failed is not None
            and failed.status == "pending_confirmation"
            and failed.version == request.expected_version
        ):
            failed.status = "stale"
            failed.version += 1
            failed.decision_action = "confirm"
            failed.decision_client_request_id = request.client_request_id
            failed.confirmed_at = moment
            failed.last_error_code = exc.code
            await db.commit()
        raise
    except Exception as exc:
        await db.rollback()
        failed = await db.scalar(
            select(AgentProposal)
            .where(
                AgentProposal.id == proposal_id,
                AgentProposal.user_id == user_id,
            )
            .with_for_update()
        )
        if failed is not None and failed.status == "pending_confirmation":
            failed.status = "failed"
            failed.version += 1
            failed.decision_action = "confirm"
            failed.decision_client_request_id = request.client_request_id
            failed.confirmed_at = moment
            failed.last_error_code = "proposal_execution_failed"
            await db.commit()
        raise PlanProposalError(
            "proposal_execution_failed", "提案执行失败，计划没有被修改"
        ) from exc
