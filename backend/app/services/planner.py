import json
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.config import settings
from app.models.profile import UserProfile
from app.models.exercise import Exercise
from app.models.workout import WorkoutPlan, PlannedExercise


async def generate_workout_plan(
    db: AsyncSession,
    *,
    user_id: str,
    goal: str | None = None,
    duration_weeks: int = 4,
) -> WorkoutPlan:
    profile = await db.scalar(select(UserProfile).where(UserProfile.user_id == user_id))

    # Pull a sample of exercises from DB to include in prompt context
    exercises = (await db.execute(select(Exercise).where(Exercise.is_active.is_(True)).limit(20))).scalars().all()
    exercise_list = [{"id": e.id, "name": e.name_zh, "category": e.category, "difficulty": e.difficulty} for e in exercises]

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

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.DEEPSEEK_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}"},
            json={"model": settings.DEEPSEEK_CHAT_MODEL, "messages": messages, "max_tokens": 2048, "temperature": 0.3},
            timeout=60.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]

    # Strip markdown fences if present
    content = content.strip()
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    content = content.strip()

    data = json.loads(content)

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
        if not exercise_id:
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
