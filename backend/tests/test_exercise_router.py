import pytest
import uuid
from app.models.exercise import Exercise


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
async def test_list_exercises_empty(client):
    resp = await client.get("/api/v1/exercises")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_exercises_returns_active(client, db_session):
    db_session.add(_make_exercise(name_en="Router Active"))
    db_session.add(_make_exercise(name_en="Router Inactive", is_active=False))
    await db_session.commit()

    resp = await client.get("/api/v1/exercises")
    assert resp.status_code == 200
    names = [e["name_en"] for e in resp.json()]
    assert "Router Active" in names
    assert "Router Inactive" not in names


@pytest.mark.asyncio
async def test_list_exercises_filter_muscle(client, db_session):
    db_session.add(_make_exercise(name_en="Router Chest", muscle_primary=["chest"]))
    db_session.add(_make_exercise(name_en="Router Legs", muscle_primary=["quads"]))
    await db_session.commit()

    resp = await client.get("/api/v1/exercises?muscle_group=chest")
    assert resp.status_code == 200
    names = [e["name_en"] for e in resp.json()]
    assert "Router Chest" in names
    assert "Router Legs" not in names


@pytest.mark.asyncio
async def test_list_exercises_limit(client, db_session):
    for i in range(5):
        db_session.add(_make_exercise(name_en=f"Router Ex {i}"))
    await db_session.commit()

    resp = await client.get("/api/v1/exercises?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) <= 2


@pytest.mark.asyncio
async def test_list_exercises_limit_exceeds_max(client):
    resp = await client.get("/api/v1/exercises?limit=100")
    assert resp.status_code == 422
