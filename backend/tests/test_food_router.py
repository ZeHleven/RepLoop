import pytest
import uuid
from app.models.food import Food


def _make_food(**kwargs) -> Food:
    defaults = dict(
        id=str(uuid.uuid4()),
        name_zh="测试食物",
        name_en="Test Food",
        category="蛋白质",
        calories_per_100g=100.0,
        protein_g=10.0,
        is_active=True,
    )
    defaults.update(kwargs)
    return Food(**defaults)


@pytest.mark.asyncio
async def test_list_foods_empty(client):
    resp = await client.get("/api/v1/foods?category=__no_such_category__")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_foods_returns_active(client, db_session):
    db_session.add(_make_food(name_en="Router Active Food"))
    db_session.add(_make_food(name_en="Router Inactive Food", is_active=False))
    await db_session.commit()

    resp = await client.get("/api/v1/foods")
    assert resp.status_code == 200
    names = [f["name_en"] for f in resp.json()]
    assert "Router Active Food" in names
    assert "Router Inactive Food" not in names


@pytest.mark.asyncio
async def test_list_foods_filter_category(client, db_session):
    db_session.add(_make_food(name_en="Router Protein", category="蛋白质"))
    db_session.add(_make_food(name_en="Router Veggie", category="蔬菜"))
    await db_session.commit()

    resp = await client.get("/api/v1/foods?category=蛋白质")
    assert resp.status_code == 200
    names = [f["name_en"] for f in resp.json()]
    assert "Router Protein" in names
    assert "Router Veggie" not in names


@pytest.mark.asyncio
async def test_list_foods_filter_min_protein(client, db_session):
    db_session.add(_make_food(name_en="Router High Protein", protein_g=40.0))
    db_session.add(_make_food(name_en="Router Low Protein", protein_g=1.0))
    await db_session.commit()

    resp = await client.get("/api/v1/foods?min_protein_g=30")
    assert resp.status_code == 200
    names = [f["name_en"] for f in resp.json()]
    assert "Router High Protein" in names
    assert "Router Low Protein" not in names


@pytest.mark.asyncio
async def test_list_foods_limit_exceeds_max(client):
    resp = await client.get("/api/v1/foods?limit=100")
    assert resp.status_code == 422
