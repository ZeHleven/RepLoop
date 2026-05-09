import pytest
from datetime import date


async def get_token(client, email):
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": "pass1234"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_log_meal_no_items(client):
    token = await get_token(client, "meal1@example.com")
    resp = await client.post(
        "/api/v1/meals",
        json={"logged_at": str(date.today()), "meal_type": "早餐", "items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["meal_type"] == "早餐"
    assert data["items"] == []


@pytest.mark.asyncio
async def test_log_meal_with_items(client):
    token = await get_token(client, "meal2@example.com")
    resp = await client.post(
        "/api/v1/meals",
        json={
            "logged_at": str(date.today()),
            "meal_type": "午餐",
            "items": [
                {"food_name": "鸡胸肉", "amount_g": 150.0, "calories": 165.0, "protein_g": 31.0, "carbs_g": 0.0, "fat_g": 3.6},
                {"food_name": "米饭", "amount_g": 200.0, "calories": 260.0, "protein_g": 5.0, "carbs_g": 57.0, "fat_g": 0.5},
            ],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["food_name"] == "鸡胸肉"


@pytest.mark.asyncio
async def test_today_summary_empty(client):
    token = await get_token(client, "meal3@example.com")
    resp = await client.get("/api/v1/meals/today", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_calories"] == 0.0
    assert data["meals"] == []


@pytest.mark.asyncio
async def test_today_summary_with_meals(client):
    token = await get_token(client, "meal4@example.com")
    today = str(date.today())
    await client.post(
        "/api/v1/meals",
        json={
            "logged_at": today,
            "meal_type": "早餐",
            "items": [{"food_name": "燕麦", "amount_g": 100.0, "calories": 389.0, "protein_g": 17.0, "carbs_g": 66.0, "fat_g": 7.0}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/meals/today", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_calories"] == 389.0
    assert data["total_protein_g"] == 17.0
    assert len(data["meals"]) == 1


@pytest.mark.asyncio
async def test_delete_meal(client):
    token = await get_token(client, "meal5@example.com")
    create_resp = await client.post(
        "/api/v1/meals",
        json={"logged_at": str(date.today()), "meal_type": "晚餐", "items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    meal_id = create_resp.json()["id"]
    del_resp = await client.delete(f"/api/v1/meals/{meal_id}", headers={"Authorization": f"Bearer {token}"})
    assert del_resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_meal_not_found(client):
    token = await get_token(client, "meal6@example.com")
    resp = await client.delete("/api/v1/meals/nonexistent", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_meals_isolated_between_users(client):
    token1 = await get_token(client, "meal7a@example.com")
    token2 = await get_token(client, "meal7b@example.com")
    await client.post(
        "/api/v1/meals",
        json={"logged_at": str(date.today()), "meal_type": "早餐", "items": []},
        headers={"Authorization": f"Bearer {token1}"},
    )
    resp = await client.get("/api/v1/meals/today", headers={"Authorization": f"Bearer {token2}"})
    assert resp.json()["meals"] == []


@pytest.mark.asyncio
async def test_history_returns_dates(client):
    token = await get_token(client, "meal8@example.com")
    today = str(date.today())
    await client.post(
        "/api/v1/meals",
        json={"logged_at": today, "meal_type": "早餐", "items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/api/v1/meals",
        json={"logged_at": today, "meal_type": "午餐", "items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/meals/history", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1  # same date → one DailySummary
    assert len(data[0]["meals"]) == 2
