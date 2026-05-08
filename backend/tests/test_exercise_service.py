import pytest
import uuid
from app.models.exercise import Exercise
from app.services.exercise import query_exercise_library


def _make_exercise(**kwargs) -> Exercise:
    defaults = dict(
        id=str(uuid.uuid4()),
        name_zh="测试动作",
        name_en="Test Exercise",
        category="力量",
        muscle_primary=["chest"],
        difficulty="中级",
        is_active=True,
    )
    defaults.update(kwargs)
    return Exercise(**defaults)


@pytest.mark.asyncio
async def test_returns_empty_when_no_match(db_session):
    result = await query_exercise_library(db_session, difficulty="__no_such_difficulty__")
    assert result == []


@pytest.mark.asyncio
async def test_returns_all_active_exercises(db_session):
    active = _make_exercise(name_en="Active One")
    inactive = _make_exercise(name_en="Inactive One", is_active=False)
    db_session.add(active)
    db_session.add(inactive)
    await db_session.commit()
    result = await query_exercise_library(db_session)
    names = [e.name_en for e in result]
    assert "Active One" in names
    assert "Inactive One" not in names


@pytest.mark.asyncio
async def test_filter_by_muscle_group(db_session):
    chest = _make_exercise(name_en="Chest Press", muscle_primary=["chest"])
    back = _make_exercise(name_en="Pull Up", muscle_primary=["back"])
    db_session.add(chest)
    db_session.add(back)
    await db_session.commit()
    result = await query_exercise_library(db_session, muscle_group="chest")
    names = [e.name_en for e in result]
    assert "Chest Press" in names
    assert "Pull Up" not in names


@pytest.mark.asyncio
async def test_filter_by_difficulty(db_session):
    beginner = _make_exercise(name_en="Easy Move", difficulty="初级")
    advanced = _make_exercise(name_en="Hard Move", difficulty="高级")
    db_session.add(beginner)
    db_session.add(advanced)
    await db_session.commit()
    result = await query_exercise_library(db_session, difficulty="初级")
    names = [e.name_en for e in result]
    assert "Easy Move" in names
    assert "Hard Move" not in names


@pytest.mark.asyncio
async def test_filter_by_equipment(db_session):
    barbell = _make_exercise(name_en="Barbell Squat", equipment=["barbell"])
    bodyweight = _make_exercise(name_en="Squat", equipment=["bodyweight"])
    db_session.add(barbell)
    db_session.add(bodyweight)
    await db_session.commit()
    result = await query_exercise_library(db_session, equipment="barbell")
    names = [e.name_en for e in result]
    assert "Barbell Squat" in names
    assert "Squat" not in names


@pytest.mark.asyncio
async def test_limit(db_session):
    for i in range(5):
        db_session.add(_make_exercise(name_en=f"Exercise {i}"))
    await db_session.commit()
    result = await query_exercise_library(db_session, limit=3)
    assert len(result) <= 3
