"""Synthetic records only; use the same tables and business clock as the product."""
from datetime import datetime, time, timedelta
import secrets

from sqlalchemy.ext.asyncio import AsyncSession
from app.models.exercise import Exercise
from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from app.models.workout import WorkoutPlan, PlannedExercise, WorkoutSession, SessionExercise
from app.services.auth import hash_password
from app.services.business_clock import business_today, SHANGHAI

SCENARIO_NAME = "小林的四周训练"
SCENARIO_DESCRIPTION = "28 岁，健身房训练，每周 3 次，目标是建立力量与稳定习惯。资料和训练记录均为虚构示例。"
QUICK_PROMPTS = ["总结我最近两周的训练情况。", "和再之前的两周相比呢？", "今天只有 20 分钟，结合我的计划给点建议。"]
MOVEMENTS = [
    ("demo-goblet-squat", "高脚杯深蹲", "Goblet Squat", "squat", ["quadriceps", "glutes"], 12.0),
    ("demo-dumbbell-row", "哑铃划船", "Dumbbell Row", "horizontal_pull", ["back"], 10.0),
    ("demo-dumbbell-press", "哑铃卧推", "Dumbbell Bench Press", "horizontal_push", ["chest"], 8.0),
]


async def seed_demo_user(db: AsyncSession) -> User:
    user = User(password_hash=hash_password(secrets.token_urlsafe(32)))
    db.add(user)
    await db.flush()
    today = business_today()
    db.add(UserProfile(user_id=user.id, age=28, gender="male", height_cm=175,
        weight_kg=72, bmi=23.51, bmi_category="normal", experience_level="beginner",
        primary_goal="general_fitness", training_days_per_week=3, session_duration_min=40,
        training_location="gym", diet_restriction="none", daily_activity_level="walking",
        injuries=[], chronic_conditions=[], onboarding_completed=True))
    for days, weight in [(27, 72.8), (20, 72.5), (13, 72.2), (6, 72.0)]:
        db.add(WeightLog(user_id=user.id, weight_kg=weight,
            recorded_at=datetime.combine(today - timedelta(days=days), time(8), SHANGHAI)))
    plan = WorkoutPlan(user_id=user.id, name="示例 · 全身力量入门", goal="general_fitness",
        duration_weeks=4, days_per_week=3, is_active=True, ai_generated=False,
        notes="虚构演示计划；周一、周三、周六，每次约 40 分钟。",
        created_at=datetime.combine(today - timedelta(days=28), time(8), SHANGHAI))
    db.add(plan)
    await db.flush()
    for identifier, name, english, pattern, muscles, weight in MOVEMENTS:
        if await db.get(Exercise, identifier) is None:
            db.add(Exercise(id=identifier, name_zh=name, name_en=english, category="strength",
                energy_category="resistance_training", energy_category_version=1,
                muscle_primary=muscles, equipment=["dumbbell"], difficulty="beginner",
                movement_pattern=pattern, is_active=True))
    await db.flush()
    for day in (1, 3, 6):
        for index, (identifier, _, _, _, _, weight) in enumerate(MOVEMENTS):
            db.add(PlannedExercise(plan_id=plan.id, day_of_week=day, exercise_id=identifier,
                sets=3, reps="10", rest_seconds=90, recommended_weight_kg=weight, order_index=index))
    for days in range(27, 0, -1):
        trained = today - timedelta(days=days)
        if trained.isoweekday() not in (1, 3, 6):
            continue
        completed = datetime.combine(trained, time(19), SHANGHAI)
        session = WorkoutSession(user_id=user.id, plan_id=plan.id, plan_name=plan.name,
            plan_family_id=plan.family_id, week_start=trained - timedelta(days=trained.weekday()),
            day_of_week=trained.isoweekday(), trained_at=trained, duration_min=35 if days > 13 else 40,
            status="completed", started_at=completed - timedelta(minutes=40), completed_at=completed,
            notes="虚构示例训练记录", feedback_data={"difficulty_feedback": "just_right", "perceived_exertion": 6, "energy_level": 4, "pain_level": 0, "pain_areas": []})
        db.add(session)
        await db.flush()
        for index, (identifier, name, _, _, _, weight) in enumerate(MOVEMENTS):
            actual_weight = weight - 2 if days > 13 else weight
            db.add(SessionExercise(session_id=session.id, exercise_id=identifier, exercise_name=name,
                order_index=index, target_sets=3, target_reps="10", target_weight_kg=actual_weight,
                rest_seconds=90, energy_category="resistance_training",
                sets_data=[{"set_number": number, "reps": 10, "weight_kg": actual_weight,
                            "completed": True} for number in (1, 2, 3)]))
    await db.flush()
    return user
