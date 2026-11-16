import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models.profile import UserProfile
from app.models.exercise import Exercise
from app.models.workout import WorkoutPlan, PlannedExercise
from app.services.ai_client import AIServiceError, chat_completion


async def generate_workout_plan(
    db: AsyncSession,
    *,
    user_id: str,
    goal: str | None = None,
    duration_weeks: int = 4,
) -> WorkoutPlan:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))

    # Pull a bounded, user-safe set of exercises into prompt context.
    exercises = (await db.execute(
        select(Exercise).where(Exercise.is_active.is_(True), Exercise.owner_id.is_(None)).limit(100)
    )).scalars().all()
    injuries = set(profile.injuries or []) if profile and isinstance(profile.injuries, list) else set()
    if injuries:
        exercises = [
            exercise for exercise in exercises
            if not injuries.intersection(set(exercise.contraindications or []))
        ]
    exercise_list = [
        {
            "id": exercise.id,
            "name": exercise.name_zh,
            "category": exercise.category,
            "difficulty": exercise.difficulty,
            "equipment": exercise.equipment or [],
        }
        for exercise in exercises
    ]
    if not exercise_list:
        raise AIServiceError("当前动作库中没有符合用户条件的可用动作")
    allowed_exercise_ids = {exercise["id"] for exercise in exercise_list}

    effective_goal = goal or (profile.primary_goal if profile else "增肌")
    days_per_week = profile.training_days_per_week if profile and profile.training_days_per_week else 3
    experience = profile.experience_level if profile else "初学者"
    location = profile.training_location if profile else "健身房"

    prompt = f"""请为以下用户生成一个{duration_weeks}周的训练计划，严格返回JSON格式，不要有任何额外文字。

用户信息：
- 目标：{effective_goal}
- 经验：{experience}
- 每周训练天数：{days_per_week}
- 训练地点：{location}
- 伤病情况：{json.dumps(profile.injuries, ensure_ascii=False) if profile and profile.injuries else "无"}
- 慢性疾病：{json.dumps(profile.chronic_conditions, ensure_ascii=False) if profile and profile.chronic_conditions else "无"}

可用动作库（从中选择合适动作）：
{json.dumps(exercise_list, ensure_ascii=False)}

返回格式（严格JSON）：
{{
  "name": "计划名称",
  "goal": "目标",
  "days_per_week": {days_per_week},
  "duration_weeks": {duration_weeks},
  "exercises": [
    {{"exercise_id": "动作ID", "day_of_week": 1, "sets": 3, "reps": "10", "rest_seconds": 90, "order_index": 0}}
  ]
}}"""

    messages = [
        {"role": "system", "content": "你是专业健身教练，只返回严格的JSON格式，不要有任何解释性文字。"},
        {"role": "user", "content": prompt},
    ]

    content = await chat_completion(
        messages,
        model=settings.DEEPSEEK_REASONING_MODEL,
        max_tokens=2048,
        temperature=0.3,
        json_mode=True,
        thinking=True,
    )

    # Strip markdown fences if present
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AIServiceError("AI 未能生成有效的训练计划数据") from exc

    plan = WorkoutPlan(
        user_id=user_id,
        name=data.get("name", f"{effective_goal}训练计划"),
        goal=data.get("goal", effective_goal),
        duration_weeks=data.get("duration_weeks", duration_weeks),
        days_per_week=data.get("days_per_week", days_per_week),
        ai_generated=True,
    )
    db.add(plan)
    await db.flush()

    for i, ex in enumerate(data.get("exercises", [])):
        exercise_id = ex.get("exercise_id")
        if not exercise_id or exercise_id not in allowed_exercise_ids:
            continue
        db.add(PlannedExercise(
            plan_id=plan.id,
            exercise_id=exercise_id,
            day_of_week=ex.get("day_of_week", 1),
            sets=ex.get("sets", 3),
            reps=str(ex.get("reps", "10")),
            rest_seconds=ex.get("rest_seconds", 90),
            order_index=ex.get("order_index", i),
        ))

    await db.commit()
    return plan
