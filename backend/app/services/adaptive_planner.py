from __future__ import annotations

import re
from dataclasses import dataclass
from statistics import mean

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.workout import PlannedExercise, SessionExercise, WorkoutSession
from app.schemas.workout import WorkoutFeedback
from app.services.personalized_planner import (
    is_exercise_compatible,
    is_exercise_safe_for_areas,
)


@dataclass(frozen=True)
class AdjustmentDecision:
    action: str
    sets: int
    reps: str
    rest_seconds: int
    recommended_weight_kg: float | None
    reason: str
    safety_priority: bool = False


@dataclass(frozen=True)
class AdaptiveAdjustmentProposal:
    planned_exercise_id: str
    exercise_id: str
    exercise_name: str
    action: str
    before: dict
    after: dict
    reason: str
    safety_priority: bool = False

    def to_response(self) -> dict:
        return {
            "exercise_id": self.exercise_id,
            "exercise_name": self.exercise_name,
            "action": self.action,
            "before": dict(self.before),
            "after": dict(self.after),
            "reason": self.reason,
            "safety_priority": self.safety_priority,
        }


def parse_rep_range(value: str | None) -> tuple[int, int]:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")[:2]]
    if not numbers:
        return 1, 1
    if len(numbers) == 1:
        return numbers[0], numbers[0]
    return min(numbers), max(numbers)


def shift_rep_range(value: str, amount: int) -> str:
    lower, upper = parse_rep_range(value)
    lower = max(1, min(1000, lower + amount))
    upper = max(lower, min(1000, upper + amount))
    return str(lower) if lower == upper else f"{lower}-{upper}"


def _valid_sets(sets_data: object) -> list[dict]:
    if not isinstance(sets_data, list):
        return []
    return [
        item for item in sets_data
        if isinstance(item, dict)
        and isinstance(item.get("reps"), int)
        and item["reps"] > 0
    ]


def _round_half(value: float) -> float:
    return round(value * 2) / 2


def decide_exercise_adjustment(
    *,
    target_sets: int,
    target_reps: str,
    rest_seconds: int,
    sets_data: object,
    feedback: WorkoutFeedback,
    current_recommended_weight_kg: float | None = None,
) -> AdjustmentDecision:
    valid_sets = _valid_sets(sets_data)
    target_sets = max(1, target_sets)
    lower_reps, upper_reps = parse_rep_range(target_reps)
    completed_sets = len(valid_sets)
    set_ratio = min(1.0, completed_sets / target_sets)
    successful_sets = sum(item["reps"] >= lower_reps for item in valid_sets)
    completion_score = min(set_ratio, successful_sets / target_sets)
    reached_top = (
        completed_sets >= target_sets
        and all(item["reps"] >= upper_reps for item in valid_sets[:target_sets])
    )
    recorded_weights = [
        float(item["weight_kg"])
        for item in valid_sets
        if isinstance(item.get("weight_kg"), (int, float))
        and float(item["weight_kg"]) > 0
    ]
    working_weight = mean(recorded_weights) if recorded_weights else current_recommended_weight_kg

    severe_pain = feedback.pain_level >= 4
    mild_pain = 0 < feedback.pain_level < 4
    subjective_hard = (
        feedback.difficulty_feedback == "too_hard"
        or (feedback.perceived_exertion is not None and feedback.perceived_exertion >= 9)
        or (feedback.energy_level is not None and feedback.energy_level <= 1)
    )
    objectively_hard = completion_score < 0.75
    objectively_easy = completion_score >= 1 and reached_top
    subjective_easy = (
        feedback.difficulty_feedback == "too_easy"
        or (
            feedback.perceived_exertion is not None
            and feedback.perceived_exertion <= 6
            and completion_score >= 1
        )
    )

    if severe_pain:
        reduced_weight = (
            max(0.5, _round_half(working_weight * 0.9))
            if working_weight is not None and working_weight > 0
            else None
        )
        return AdjustmentDecision(
            action="reduce_for_pain",
            sets=max(1, target_sets - 1),
            reps=shift_rep_range(target_reps, -1),
            rest_seconds=min(600, rest_seconds + 30),
            recommended_weight_kg=reduced_weight,
            reason="疼痛反馈优先：下一练降低训练量并延长休息；若仍不适请停止该动作。",
            safety_priority=True,
        )

    if mild_pain:
        cautious_weight = (
            max(0.5, _round_half(working_weight * 0.95))
            if objectively_hard and working_weight is not None and working_weight > 0
            else _round_half(working_weight) if working_weight else None
        )
        return AdjustmentDecision(
            action="reduce_for_caution" if objectively_hard else "hold_for_caution",
            sets=max(1, target_sets - 1) if objectively_hard else target_sets,
            reps=shift_rep_range(target_reps, -1) if objectively_hard else target_reps,
            rest_seconds=min(600, rest_seconds + (30 if objectively_hard else 15)),
            recommended_weight_kg=cautious_weight,
            reason=(
                "轻微不适且完成度不足，下一练保守降量并增加休息时间。"
                if objectively_hard
                else "记录到轻微不适，下一练暂不加量并增加休息时间。"
            ),
            safety_priority=True,
        )

    if subjective_hard or objectively_hard:
        severe = completion_score < 0.5
        next_sets = max(1, target_sets - 1) if completion_score < 0.75 else target_sets
        if working_weight is not None and working_weight > 0:
            factor = 0.9 if severe else 0.95
            next_weight = max(0.5, _round_half(working_weight * factor))
            return AdjustmentDecision(
                action="decrease_weight" if next_sets == target_sets else "decrease_weight_and_sets",
                sets=next_sets,
                reps=target_reps,
                rest_seconds=min(600, rest_seconds + (30 if severe else 15)),
                recommended_weight_kg=next_weight,
                reason="本次完成度或主观难度偏高，下一练降低重量并给予更多恢复。",
            )
        return AdjustmentDecision(
            action="decrease_volume",
            sets=next_sets,
            reps=shift_rep_range(target_reps, -2 if severe else -1),
            rest_seconds=min(600, rest_seconds + (30 if severe else 15)),
            recommended_weight_kg=None,
            reason="本次完成度或主观难度偏高，下一练降低组次目标。",
        )

    if subjective_easy or objectively_easy:
        if working_weight is not None and working_weight > 0:
            factor = 1.05 if feedback.difficulty_feedback == "too_easy" else 1.025
            next_weight = max(
                _round_half(working_weight + 0.5),
                _round_half(working_weight * factor),
            )
            return AdjustmentDecision(
                action="increase_weight",
                sets=target_sets,
                reps=target_reps,
                rest_seconds=rest_seconds,
                recommended_weight_kg=next_weight,
                reason="本次完成目标且强度可控，下一练小幅增加建议重量。",
            )
        return AdjustmentDecision(
            action="increase_reps",
            sets=target_sets,
            reps=shift_rep_range(target_reps, 2),
            rest_seconds=rest_seconds,
            recommended_weight_kg=None,
            reason="徒手动作完成稳定，下一练小幅增加目标次数。",
        )

    return AdjustmentDecision(
        action="maintain",
        sets=target_sets,
        reps=target_reps,
        rest_seconds=rest_seconds,
        recommended_weight_kg=_round_half(working_weight) if working_weight else None,
        reason="本次完成度与主观强度处于合适区间，下一练维持当前目标。",
    )


def _snapshot(planned: PlannedExercise, exercise: Exercise) -> dict:
    return {
        "exercise_id": exercise.id,
        "exercise_name": exercise.name_zh,
        "sets": planned.sets,
        "reps": planned.reps,
        "rest_seconds": planned.rest_seconds,
        "recommended_weight_kg": planned.recommended_weight_kg,
    }


def _adjusted_snapshot(
    *,
    exercise: Exercise,
    sets: int,
    reps: str,
    rest_seconds: int,
    recommended_weight_kg: float | None,
) -> dict:
    return {
        "exercise_id": exercise.id,
        "exercise_name": exercise.name_zh,
        "sets": sets,
        "reps": reps,
        "rest_seconds": rest_seconds,
        "recommended_weight_kg": recommended_weight_kg,
    }


def _replacement_candidate(
    current: Exercise,
    candidates: list[Exercise],
    *,
    profile: UserProfile | None,
    pain_areas: set[str],
    used_ids: set[str],
) -> Exercise | None:
    safe = []
    for item in candidates:
        if item.id == current.id or item.id in used_ids:
            continue
        if profile is not None:
            if not is_exercise_compatible(profile, item, extra_injuries=pain_areas):
                continue
        elif (
            not is_exercise_safe_for_areas(item, pain_areas)
            or item.category in {"有氧", "cardio"}
            or item.movement_pattern == "isometric"
        ):
            continue
        safe.append(item)
    safe.sort(key=lambda item: (
        0 if item.movement_pattern == current.movement_pattern else 1,
        0 if item.category == current.category else 1,
        0 if item.difficulty == current.difficulty else 1,
        item.name_zh,
    ))
    return safe[0] if safe else None


async def build_adaptive_adjustment_proposals(
    db: AsyncSession,
    *,
    session: WorkoutSession,
    session_exercises: list[SessionExercise],
    feedback: WorkoutFeedback,
) -> list[AdaptiveAdjustmentProposal]:
    """Calculate proposed plan changes without mutating ORM entities."""
    if session.plan_id is None or session.day_of_week is None:
        return []

    planned = (await db.execute(
        select(PlannedExercise)
        .where(
            PlannedExercise.plan_id == session.plan_id,
            PlannedExercise.day_of_week == session.day_of_week,
        )
        .order_by(PlannedExercise.order_index)
    )).scalars().all()
    if not planned:
        return []

    profile = await db.scalar(
        select(UserProfile).where(UserProfile.user_id == session.user_id)
    )
    candidates = (await db.execute(
        select(Exercise)
        .where(Exercise.is_active.is_(True), Exercise.owner_id.is_(None))
        .order_by(Exercise.name_zh)
        .limit(200)
    )).scalars().all()
    exercise_by_id = {item.id: item for item in candidates}
    used_ids = {item.exercise_id for item in planned}
    remaining_session_items = list(session_exercises)
    pain_areas = set(feedback.pain_areas)
    proposals: list[AdaptiveAdjustmentProposal] = []

    for planned_item in planned:
        session_item = next(
            (
                item for item in remaining_session_items
                if item.order_index == planned_item.order_index
                and item.exercise_id == planned_item.exercise_id
            ),
            None,
        )
        if session_item is None:
            session_item = next(
                (item for item in remaining_session_items if item.exercise_id == planned_item.exercise_id),
                None,
            )
        if session_item is not None:
            remaining_session_items.remove(session_item)
        current = exercise_by_id.get(planned_item.exercise_id)
        if session_item is None or current is None:
            continue
        before = _snapshot(planned_item, current)
        should_replace = (
            feedback.pain_level >= 4
            and bool(pain_areas)
            and not is_exercise_safe_for_areas(current, pain_areas)
        )
        replacement = _replacement_candidate(
            current,
            candidates,
            profile=profile,
            pain_areas=pain_areas,
            used_ids=used_ids,
        ) if should_replace else None

        if replacement is not None:
            used_ids.discard(current.id)
            used_ids.add(replacement.id)
            after = _adjusted_snapshot(
                exercise=replacement,
                sets=max(1, min(2, planned_item.sets)),
                reps=planned_item.reps,
                rest_seconds=max(120, planned_item.rest_seconds),
                recommended_weight_kg=None,
            )
            proposals.append(AdaptiveAdjustmentProposal(
                planned_exercise_id=planned_item.id,
                exercise_id=current.id,
                exercise_name=current.name_zh,
                action="replace_exercise",
                before=before,
                after=after,
                reason=f"疼痛部位（{'、'.join(sorted(pain_areas))}）与原动作存在明显冲突，已优先替换为更安全动作。",
                safety_priority=True,
            ))
            continue

        decision = decide_exercise_adjustment(
            target_sets=planned_item.sets,
            target_reps=planned_item.reps,
            rest_seconds=planned_item.rest_seconds,
            sets_data=session_item.sets_data,
            feedback=feedback,
            current_recommended_weight_kg=planned_item.recommended_weight_kg,
        )
        proposals.append(AdaptiveAdjustmentProposal(
            planned_exercise_id=planned_item.id,
            exercise_id=current.id,
            exercise_name=current.name_zh,
            action=decision.action,
            before=before,
            after=_adjusted_snapshot(
                exercise=current,
                sets=decision.sets,
                reps=decision.reps,
                rest_seconds=decision.rest_seconds,
                recommended_weight_kg=decision.recommended_weight_kg,
            ),
            reason=decision.reason,
            safety_priority=decision.safety_priority,
        ))

    return proposals


async def apply_adjustment_proposals(
    db: AsyncSession,
    *,
    proposals: list[AdaptiveAdjustmentProposal],
) -> list[dict]:
    """Apply previously calculated proposals; the caller owns the transaction."""
    if not proposals:
        return []

    proposal_by_id = {item.planned_exercise_id: item for item in proposals}
    planned = (await db.execute(
        select(PlannedExercise).where(PlannedExercise.id.in_(proposal_by_id))
    )).scalars().all()
    planned_by_id = {item.id: item for item in planned}
    results: list[dict] = []
    for proposal in proposals:
        planned_item = planned_by_id.get(proposal.planned_exercise_id)
        if planned_item is None:
            continue
        planned_item.exercise_id = proposal.after["exercise_id"]
        planned_item.sets = proposal.after["sets"]
        planned_item.reps = proposal.after["reps"]
        planned_item.rest_seconds = proposal.after["rest_seconds"]
        planned_item.recommended_weight_kg = proposal.after[
            "recommended_weight_kg"
        ]
        results.append(proposal.to_response())

    return results


async def apply_adaptive_adjustments(
    db: AsyncSession,
    *,
    session: WorkoutSession,
    session_exercises: list[SessionExercise],
    feedback: WorkoutFeedback,
) -> list[dict]:
    """Compatibility entry point for the existing training-completion flow."""
    proposals = await build_adaptive_adjustment_proposals(
        db,
        session=session,
        session_exercises=session_exercises,
        feedback=feedback,
    )
    return await apply_adjustment_proposals(db, proposals=proposals)
