import pytest
from datetime import timedelta
from app.services.training_lifecycle import training_today


async def get_token(client, email):
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": "pass1234"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_log_meal_no_items(client):
    token = await get_token(client, "meal1@example.com")
    resp = await client.post(
        "/api/v1/meals",
        json={"logged_at": str(training_today()), "meal_type": "早餐", "items": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_log_meal_with_items(client):
    token = await get_token(client, "meal2@example.com")
    resp = await client.post(
        "/api/v1/meals",
        json={
            "logged_at": str(training_today()),
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
    today = str(training_today())
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
        json={"logged_at": str(training_today()), "meal_type": "晚餐", "items": [
            {"food_name": "米饭", "amount_g": 100, "calories": 130,
             "protein_g": 2.5, "carbs_g": 28, "fat_g": 0.3}
        ]},
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
        json={"logged_at": str(training_today()), "meal_type": "早餐", "items": [
            {"food_name": "燕麦", "amount_g": 50, "calories": 190,
             "protein_g": 6, "carbs_g": 32, "fat_g": 4}
        ]},
        headers={"Authorization": f"Bearer {token1}"},
    )
    resp = await client.get("/api/v1/meals/today", headers={"Authorization": f"Bearer {token2}"})
    assert resp.json()["meals"] == []


@pytest.mark.asyncio
async def test_history_returns_dates(client):
    token = await get_token(client, "meal8@example.com")
    today = str(training_today())
    await client.post(
        "/api/v1/meals",
        json={"logged_at": today, "meal_type": "早餐", "items": [
            {"food_name": "燕麦", "amount_g": 50, "calories": 190,
             "protein_g": 6, "carbs_g": 32, "fat_g": 4}
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    await client.post(
        "/api/v1/meals",
        json={"logged_at": today, "meal_type": "午餐", "items": [
            {"food_name": "米饭", "amount_g": 100, "calories": 130,
             "protein_g": 2.5, "carbs_g": 28, "fat_g": 0.3}
        ]},
        headers={"Authorization": f"Bearer {token}"},
    )
    resp = await client.get("/api/v1/meals/history", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1  # same date → one DailySummary
    assert len(data[0]["meals"]) == 2


@pytest.mark.asyncio
async def test_update_meal_replaces_all_items_and_recalculates_standard_food(
    client, db_session
):
    from app.models.food import Food

    food = Food(
        id="meal-update-standard-food",
        name_zh="标准燕麦",
        category="主食",
        calories_per_100g=380,
        protein_g=13,
        carbs_g=68,
        fat_g=7,
        is_active=True,
    )
    db_session.add(food)
    await db_session.commit()
    token = await get_token(client, "meal-update@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api/v1/meals",
        json={
            "logged_at": str(training_today()),
            "meal_type": "早餐",
            "items": [{
                "food_name": "客户端伪造名称",
                "food_id": food.id,
                "amount_g": 50,
                "calories": 9999,
                "protein_g": 999,
                "carbs_g": 999,
                "fat_g": 999,
            }],
        },
        headers=headers,
    )
    meal_id = created.json()["id"]

    updated = await client.put(
        f"/api/v1/meals/{meal_id}",
        json={
            "logged_at": str(training_today() - timedelta(days=1)),
            "meal_type": "加餐",
            "items": [
                {
                    "food_name": "仍然伪造",
                    "food_id": food.id,
                    "amount_g": 150,
                    "calories": 1,
                    "protein_g": 1,
                    "carbs_g": 1,
                    "fat_g": 1,
                },
                {
                    "food_name": "自定义酸奶",
                    "amount_g": 120,
                    "calories": 86,
                    "protein_g": 5,
                    "carbs_g": 10,
                    "fat_g": 2,
                },
            ],
        },
        headers=headers,
    )
    assert updated.status_code == 200
    payload = updated.json()
    assert payload["meal_type"] == "加餐"
    assert payload["items"][0]["food_name"] == "标准燕麦"
    assert payload["items"][0]["calories"] == 570
    assert payload["items"][0]["protein_g"] == 19.5
    assert len(payload["items"]) == 2


@pytest.mark.asyncio
async def test_update_meal_validation_failure_keeps_original_items(client):
    token = await get_token(client, "meal-update-rollback@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api/v1/meals",
        json={
            "logged_at": str(training_today()),
            "meal_type": "午餐",
            "items": [{
                "food_name": "原餐",
                "amount_g": 100,
                "calories": 120,
                "protein_g": 8,
                "carbs_g": 15,
                "fat_g": 3,
            }],
        },
        headers=headers,
    )
    meal_id = created.json()["id"]
    rejected = await client.put(
        f"/api/v1/meals/{meal_id}",
        json={
            "logged_at": str(training_today()),
            "meal_type": "晚餐",
            "items": [{
                "food_id": "missing-food",
                "food_name": "不存在",
                "amount_g": 100,
                "calories": 0,
                "protein_g": 0,
                "carbs_g": 0,
                "fat_g": 0,
            }],
        },
        headers=headers,
    )
    assert rejected.status_code == 400
    history = await client.get("/api/v1/meals/history", headers=headers)
    saved = next(
        meal
        for day in history.json()
        for meal in day["meals"]
        if meal["id"] == meal_id
    )
    assert saved["meal_type"] == "午餐"
    assert saved["items"][0]["food_name"] == "原餐"


@pytest.mark.asyncio
async def test_update_meal_rejects_records_outside_thirty_days(client):
    token = await get_token(client, "meal-update-old@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = await client.post(
        "/api/v1/meals",
        json={
            "logged_at": str(training_today() - timedelta(days=30)),
            "meal_type": "午餐",
            "items": [{
                "food_name": "旧餐",
                "amount_g": 100,
                "calories": 120,
                "protein_g": 8,
                "carbs_g": 15,
                "fat_g": 3,
            }],
        },
        headers=headers,
    )
    rejected = await client.put(
        f"/api/v1/meals/{created.json()['id']}",
        json={
            "logged_at": str(training_today()),
            "meal_type": "晚餐",
            "items": [{
                "food_name": "新餐",
                "amount_g": 100,
                "calories": 120,
                "protein_g": 8,
                "carbs_g": 15,
                "fat_g": 3,
            }],
        },
        headers=headers,
    )
    assert rejected.status_code == 409
