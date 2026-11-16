from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.services.exercise_search import selection_metadata
from app.schemas.workout import (
    PersonalizedExerciseOption,
    PersonalizedPlanExercise,
    PersonalizedPlanPreview,
    PersonalizedPlanPreviewRequest,
)


class PersonalizedPlanError(ValueError):
    """Raised when a safe, usable personalized plan cannot be produced."""


GOAL_LABELS = {
    "fat_loss": "减脂",
    "muscle_gain": "增肌",
    "strength": "力量提升",
    "endurance": "耐力提升",
    "flexibility": "灵活性改善",
    "general_fitness": "综合体能",
}

EXPERIENCE_LABELS = {
    "beginner": "新手",
    "intermediate": "进阶训练者",
    "advanced": "熟练训练者",
}

LOCATION_LABELS = {
    "gym": "健身房",
    "home": "居家",
    "outdoor": "户外",
}

LOCATION_EQUIPMENT = {
    "home": {"bodyweight"},
    "outdoor": {"bodyweight", "pull_up_bar"},
}

INJURY_RISK_KEYWORDS = {
    "膝关节": {"深蹲", "腿举", "分腿蹲", "跑步"},
    "肩关节": {"卧推", "肩上推举", "引体向上"},
    "腰背部": {"硬拉", "俯身划船", "深蹲"},
    "踝关节": {"跑步", "提踵", "分腿蹲"},
    "腕肘部": {"卧推", "肩上推举", "俯卧撑", "弯举", "下压"},
}

def _health_values(values: object) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip() and value != "none"}


def _location_compatible(location: str, exercise: Exercise) -> bool:
    allowed = LOCATION_EQUIPMENT.get(location)
    if allowed is None:
        return True
    equipment = set(exercise.equipment or [])
    return not equipment or equipment.issubset(allowed)


def _injury_compatible(injuries: set[str], exercise: Exercise) -> bool:
    contraindications = {str(value).strip() for value in exercise.contraindications or []}
    if injuries.intersection(contraindications):
        return False
    description = exercise.name_zh
    if exercise.owner_id:
        description += ' ' + (exercise.technique_cues or '')
    for injury in injuries:
        if any(keyword in description for keyword in INJURY_RISK_KEYWORDS.get(injury, set())):
            return False
    return True


def is_exercise_safe_for_areas(exercise: Exercise, areas: set[str]) -> bool:
    return _injury_compatible(areas, exercise)


def is_exercise_compatible(
    profile: UserProfile,
    exercise: Exercise,
    *,
    extra_injuries: set[str] | None = None,
) -> bool:
    if exercise.owner_id and exercise.owner_id != profile.user_id:
        return False
    injuries = _health_values(profile.injuries).union(extra_injuries or set())
    if exercise.owner_id and _health_values(profile.chronic_conditions).intersection(exercise.contraindications or []):
        return False
    location = profile.training_location or "gym"
    if not _location_compatible(location, exercise):
        return False
    if not _injury_compatible(injuries, exercise):
        return False
    if (profile.experience_level or "beginner") == "beginner" and exercise.difficulty in {
        "高级",
        "advanced",
    }:
        return False
    # The current workout logger records repetitions, not cardio/isometric duration.
    return (
        exercise.category not in {"有氧", "cardio"}
        and exercise.movement_pattern != "isometric"
    )


def _difficulty_score(experience: str, exercise: Exercise) -> int:
    difficulty = exercise.difficulty
    if experience == "advanced":
        order = {"高级": 0, "advanced": 0, "中级": 1, "intermediate": 1, "初级": 2, "beginner": 2}
    elif experience == "intermediate":
        order = {"中级": 0, "intermediate": 0, "初级": 1, "beginner": 1, "高级": 2, "advanced": 2}
    else:
        order = {"初级": 0, "beginner": 0, "中级": 1, "intermediate": 1, "高级": 3, "advanced": 3}
    return order.get(difficulty, 2)


def _schedule_days(days_per_week: int) -> list[int]:
    schedules = {
        1: [1],
        2: [1, 4],
        3: [1, 3, 5],
        4: [1, 2, 4, 6],
        5: [1, 2, 3, 5, 6],
        6: [1, 2, 3, 4, 5, 6],
        7: [1, 2, 3, 4, 5, 6, 7],
    }
    return schedules[days_per_week]


def _target_reps(exercise: Exercise, goal: str) -> str:
    minimum = exercise.rep_range_min
    maximum = exercise.rep_range_max
    if goal == "strength":
        target_min, target_max = 5, 8
    elif goal == "muscle_gain":
        target_min, target_max = 8, 12
    elif goal in {"fat_loss", "endurance"}:
        target_min, target_max = 12, 15
    else:
        target_min, target_max = 8, 12

    if minimum is not None:
        target_min = max(target_min, minimum)
    if maximum is not None:
        target_max = min(target_max, maximum)
    if target_min > target_max:
        target_min = minimum or target_max
        target_max = maximum or target_min
    return str(target_min) if target_min == target_max else f"{target_min}-{target_max}"


def _target_sets(experience: str, medical_caution: bool, exercise: Exercise) -> int:
    if medical_caution or experience == "beginner":
        return 2
    baseline = 4 if experience == "advanced" else 3
    if exercise.sets_range_min is not None:
        baseline = max(baseline, exercise.sets_range_min)
    if exercise.sets_range_max is not None:
        baseline = min(baseline, exercise.sets_range_max)
    return max(1, min(5, baseline))


def _rest_seconds(goal: str, medical_caution: bool) -> int:
    if medical_caution:
        return 120
    if goal == "strength":
        return 120
    if goal in {"fat_loss", "endurance"}:
        return 60
    return 90


def _select_day_exercises(
    exercises: Sequence[Exercise],
    *,
    count: int,
    day_index: int,
) -> list[Exercise]:
    patterns = ["squat", "push", "pull", "hinge", "isometric", "flex", None]
    rotated = patterns[day_index % 4:] + patterns[:day_index % 4]
    selected: list[Exercise] = []

    for pattern in rotated:
        if len(selected) >= count:
            break
        match = next(
            (
                item for item in exercises
                if item not in selected and (pattern is None or item.movement_pattern == pattern)
            ),
            None,
        )
        if match is not None:
            selected.append(match)

    if len(selected) < count:
        selected.extend(item for item in exercises if item not in selected)
    return selected[:count]


def build_personalized_plan_preview(
    profile: UserProfile,
    exercises: Sequence[Exercise],
    request: PersonalizedPlanPreviewRequest | None = None,
) -> PersonalizedPlanPreview:
    request = request or PersonalizedPlanPreviewRequest()
    if not profile.onboarding_completed:
        raise PersonalizedPlanError("请先完成新用户资料与健康筛查")

    goal = request.goal or profile.primary_goal or "general_fitness"
    days_per_week = (len(request.training_days) if request.training_days is not None
                     else request.days_per_week or profile.training_days_per_week or 3)
    session_duration = request.session_duration_min or profile.session_duration_min or 45
    experience = profile.experience_level or "beginner"
    injuries = _health_values(profile.injuries)
    chronic_conditions = _health_values(profile.chronic_conditions)
    medical_caution = bool(chronic_conditions) or bool(profile.age and profile.age >= 60)

    compatible = [item for item in exercises if is_exercise_compatible(profile, item)]
    compatible.sort(key=lambda item: (_difficulty_score(experience, item), item.name_zh))
    if not compatible:
        raise PersonalizedPlanError("动作库中没有同时满足训练地点与健康条件的动作")

    exercise_count = max(3, min(6, session_duration // 10))
    if experience == "beginner" or medical_caution:
        exercise_count = min(exercise_count, 4)
    exercise_count = min(exercise_count, len(compatible))

    planned: list[PersonalizedPlanExercise] = []
    schedule = request.training_days if request.training_days is not None else _schedule_days(days_per_week)
    for day_index, day_of_week in enumerate(schedule):
        # Rotate the stable list so repeated full-body days do not all start identically.
        offset = day_index % len(compatible)
        rotated = [*compatible[offset:], *compatible[:offset]]
        for order_index, exercise in enumerate(
            _select_day_exercises(rotated, count=exercise_count, day_index=day_index)
        ):
            planned.append(PersonalizedPlanExercise(
                exercise_id=exercise.id,
                exercise_name=exercise.name_zh,
                category=exercise.category,
                day_of_week=day_of_week,
                sets=_target_sets(experience, medical_caution, exercise),
                reps=_target_reps(exercise, goal),
                rest_seconds=_rest_seconds(goal, medical_caution),
                order_index=order_index,
            ))

    goal_label = GOAL_LABELS.get(goal, goal)
    experience_label = EXPERIENCE_LABELS.get(experience, experience)
    location_label = LOCATION_LABELS.get(profile.training_location or "gym", profile.training_location or "健身房")
    rationale = [
        f"围绕“{goal_label}”安排动作次数与组间休息。",
        f"按{experience_label}强度起步，每周 {days_per_week} 练、单次约 {session_duration} 分钟。",
        f"动作器械已按{location_label}训练场景筛选。",
        "首轮不预设训练重量，将用第一练的逐组记录校准后续重量参考。",
    ]
    if request.training_days is not None:
        labels = [f"周{'一二三四五六日'[day - 1]}" for day in schedule]
        rationale.append(f"按你选择的{'、'.join(labels)}安排训练。")
    if injuries:
        rationale.append(f"已根据健康筛查避开与{ '、'.join(sorted(injuries)) }明显冲突的动作。")

    safety_notes = ["训练建议不能替代医生诊断；出现疼痛、胸闷或明显不适时请立即停止。"]
    if injuries:
        safety_notes.insert(0, f"已记录伤病：{'、'.join(sorted(injuries))}；首次训练请使用保守重量。")
    if chronic_conditions:
        safety_notes.insert(0, f"已记录慢性疾病：{'、'.join(sorted(chronic_conditions))}；开始计划前建议咨询医生。")
    if profile.age and profile.age >= 60:
        safety_notes.insert(0, "已采用更保守的起始训练量；首次训练建议有人陪同并延长热身。")

    return PersonalizedPlanPreview(
        name=f"{goal_label} · {days_per_week}日训练计划",
        goal=goal,
        duration_weeks=request.duration_weeks,
        days_per_week=days_per_week,
        session_duration_min=session_duration,
        rationale=rationale,
        safety_notes=safety_notes,
        exercises=planned,
        exercise_options=[
            PersonalizedExerciseOption(
                **selection_metadata(item),
                exercise_id=item.id,
                exercise_name=item.name_zh,
                category=item.category,
                difficulty=item.difficulty,
                equipment=list(item.equipment or []),
            )
            for item in compatible
        ],
    )


async def preview_personalized_plan(
    db: AsyncSession,
    *,
    user_id: str,
    request: PersonalizedPlanPreviewRequest,
) -> PersonalizedPlanPreview:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))
    if profile is None:
        raise PersonalizedPlanError("请先完善训练档案")
    exercises = (await db.execute(
        select(Exercise)
        .where(Exercise.is_active.is_(True), Exercise.owner_id.is_(None))
        .order_by(Exercise.name_zh)
        .limit(200)
    )).scalars().all()
    return build_personalized_plan_preview(profile, exercises, request)


def validate_personalized_selection(
    profile: UserProfile,
    exercises: Sequence[Exercise],
) -> None:
    incompatible = [item.name_zh for item in exercises if not is_exercise_compatible(profile, item)]
    if incompatible:
        raise PersonalizedPlanError(
            f"以下动作不符合当前训练地点或健康筛查限制：{'、'.join(incompatible)}"
        )
