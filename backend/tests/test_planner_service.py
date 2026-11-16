import json
import pytest
from unittest.mock import patch, AsyncMock
from app.models.user import User
from app.models.workout import WorkoutPlan, PlannedExercise
from app.models.exercise import Exercise
from app.services.planner import generate_workout_plan
import bcrypt


def _make_user(uid: str, email: str) -> User:
    pw = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    return User(id=uid, email=email, password_hash=pw)


def _make_exercise(eid: str, name: str) -> Exercise:
    return Exercise(
        id=eid,
        name_zh=name,
        name_en=name,
        category="strength",
        muscle_primary=["chest"],
        difficulty="beginner",
    )


MOCK_AI_PLAN = {
    "name": "AI plan",
    "goal": "fat_loss",
    "days_per_week": 3,
    "duration_weeks": 4,
    "exercises": [
        {"exercise_id": "ex-001", "day_of_week": 1, "sets": 3, "reps": "12", "rest_seconds": 60, "order_index": 0},
        {"exercise_id": "ex-001", "day_of_week": 3, "sets": 3, "reps": "12", "rest_seconds": 60, "order_index": 0},
    ],
}


@pytest.mark.asyncio
async def test_generate_creates_plan_and_exercises(db_session):
    db_session.add(_make_user("planner-u1", "planner1@example.com"))
    db_session.add(_make_exercise("ex-001", "Push-up"))
    await db_session.commit()

    with patch(
        "app.services.planner.chat_completion",
        new=AsyncMock(return_value=json.dumps(MOCK_AI_PLAN)),
    ):
        plan = await generate_workout_plan(db_session, user_id="planner-u1", goal="fat_loss", duration_weeks=4)

    assert plan.ai_generated is True
    assert plan.name == "AI plan"
    assert plan.goal == "fat_loss"

    from sqlalchemy import select
    exs = (await db_session.execute(
        select(PlannedExercise).where(PlannedExercise.plan_id == plan.id)
    )).scalars().all()
    assert len(exs) == 2


@pytest.mark.asyncio
async def test_generate_uses_profile_defaults(db_session):
    db_session.add(_make_user("planner-u2", "planner2@example.com"))
    db_session.add(_make_exercise("ex-002", "Squat"))
    await db_session.commit()

    payload = {"name": "Default Plan", "goal": "muscle_gain", "days_per_week": 3, "duration_weeks": 4, "exercises": []}
    with patch(
        "app.services.planner.chat_completion",
        new=AsyncMock(return_value=json.dumps(payload)),
    ):
        plan = await generate_workout_plan(db_session, user_id="planner-u2")

    assert plan is not None
    assert plan.ai_generated is True


@pytest.mark.asyncio
async def test_generate_handles_markdown_fenced_json(db_session):
    db_session.add(_make_user("planner-u3", "planner3@example.com"))
    db_session.add(_make_exercise("ex-003", "Row"))
    await db_session.commit()

    payload = {"name": "Fenced Plan", "goal": "fat_loss", "days_per_week": 3, "duration_weeks": 4, "exercises": []}
    fenced = "```json\n" + json.dumps(payload) + "\n```"

    with patch(
        "app.services.planner.chat_completion",
        new=AsyncMock(return_value=fenced),
    ):
        plan = await generate_workout_plan(db_session, user_id="planner-u3", goal="fat_loss")

    assert plan.name == "Fenced Plan"
