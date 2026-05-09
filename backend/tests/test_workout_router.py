import pytest
from datetime import date


async def get_token(client, email):
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": "pass1234"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_create_plan(client):
    token = await get_token(client, "wo1@example.com")
    resp = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "减脂计划", "goal": "减脂", "duration_weeks": 8, "days_per_week": 4},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "减脂计划"
    assert data["goal"] == "减脂"
    assert data["ai_generated"] is False


@pytest.mark.asyncio
async def test_list_plans_empty(client):
    token = await get_token(client, "wo2@example.com")
    resp = await client.get("/api/v1/workouts/plans", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_plans_returns_created(client):
    token = await get_token(client, "wo3@example.com")
    await client.post(
        "/api/v1/workouts/plans",
        json={"name": "计划A"},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/api/v1/workouts/plans",
        json={"name": "计划B"},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/workouts/plans", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_plan_detail(client):
    token = await get_token(client, "wo4@example.com")
    create_resp = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "详情计划"},
        headers={"Authorization": f"Bearer {token}"},
    )
    plan_id = create_resp.json()["id"]
    resp = await client.get(f"/api/v1/workouts/plans/{plan_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["id"] == plan_id
    assert "exercises" in resp.json()


@pytest.mark.asyncio
async def test_get_plan_not_found(client):
    token = await get_token(client, "wo5@example.com")
    resp = await client.get("/api/v1/workouts/plans/nonexistent", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_plan(client):
    token = await get_token(client, "wo6@example.com")
    create_resp = await client.post(
        "/api/v1/workouts/plans",
        json={"name": "待删除计划"},
        headers={"Authorization": f"Bearer {token}"},
    )
    plan_id = create_resp.json()["id"]
    del_resp = await client.delete(f"/api/v1/workouts/plans/{plan_id}", headers={"Authorization": f"Bearer {token}"})
    assert del_resp.status_code == 204
    get_resp = await client.get(f"/api/v1/workouts/plans/{plan_id}", headers={"Authorization": f"Bearer {token}"})
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_log_session(client):
    token = await get_token(client, "wo7@example.com")
    resp = await client.post(
        "/api/v1/workouts/sessions",
        json={"trained_at": str(date.today()), "duration_min": 45, "notes": "感觉不错"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["duration_min"] == 45
    assert data["notes"] == "感觉不错"


@pytest.mark.asyncio
async def test_list_sessions(client):
    token = await get_token(client, "wo8@example.com")
    await client.post(
        "/api/v1/workouts/sessions",
        json={"trained_at": str(date.today())},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/workouts/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_plans_isolated_between_users(client):
    token1 = await get_token(client, "wo9a@example.com")
    token2 = await get_token(client, "wo9b@example.com")
    await client.post("/api/v1/workouts/plans", json={"name": "用户1计划"}, headers={"Authorization": f"Bearer {token1}"})
    resp = await client.get("/api/v1/workouts/plans", headers={"Authorization": f"Bearer {token2}"})
    assert resp.json() == []
