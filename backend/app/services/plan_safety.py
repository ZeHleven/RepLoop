from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.models.workout import PlannedExercise, WorkoutPlan
from app.services.personalized_planner import is_exercise_compatible


@dataclass(frozen=True)
class PlanSafetyEvaluation:
    status: str
    reasons: tuple[str, ...]


async def evaluate_plan_safety(
    db: AsyncSession,
    *,
    plan: WorkoutPlan,
    profile: UserProfile | None = None,
    planned_exercises: list[PlannedExercise] | None = None,
) -> PlanSafetyEvaluation:
    """Evaluate a plan against the user's current server-side safety context."""
    if profile is None:
        profile = await db.scalar(
            select(UserProfile).where(UserProfile.user_id == plan.user_id)
        )
    # Existing users may have plans created before profile onboarding existed.
    # Absence of a profile is not itself a known incompatibility.  A partial
    # profile is still evaluated so any saved injury data takes effect at once.
    if profile is None:
        return PlanSafetyEvaluation(status="compatible", reasons=())

    planned = planned_exercises
    if planned is None:
        planned = list((await db.execute(
            select(PlannedExercise).where(PlannedExercise.plan_id == plan.id)
        )).scalars().all())
    if not planned:
        return PlanSafetyEvaluation(
            status="needs_review",
            reasons=("计划中没有可执行的训练动作",),
        )

    exercise_ids = {item.exercise_id for item in planned}
    exercises = list((await db.execute(
        select(Exercise).where(Exercise.id.in_(exercise_ids))
    )).scalars().all())
    by_id = {item.id: item for item in exercises}

    reasons: list[str] = []
    for exercise_id in sorted(exercise_ids):
        exercise = by_id.get(exercise_id)
        if exercise is None or not exercise.is_active or (exercise.owner_id and exercise.owner_id != plan.user_id):
            reasons.append("计划包含已下架或不存在的动作")
            continue
        if not is_exercise_compatible(profile, exercise):
            reasons.append(f"“{exercise.name_zh}”不符合当前健康或训练条件")

    normalized = tuple(dict.fromkeys(reasons))
    return PlanSafetyEvaluation(
        status="needs_review" if normalized else "compatible",
        reasons=normalized,
    )
