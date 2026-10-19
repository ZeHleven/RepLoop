import pytest


async def register_and_get_token(client, email="profile@example.com"):
    resp = await client.post("/api/v1/auth/register", json={
        "email": email, "password": "password123"
    })
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_get_empty_profile(client):
    token = await register_and_get_token(client)
    resp = await client.get("/api/v1/profile", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bmi"] is None
    assert data["onboarding_completed"] is False


@pytest.mark.asyncio
async def test_update_profile_calculates_bmi(client):
    token = await register_and_get_token(client, "bmi@example.com")
    resp = await client.put("/api/v1/profile", json={
        "height_cm": 175,
        "weight_kg": 70,
        "age": 28,
        "gender": "male",
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["bmi"] == round(70 / (1.75 ** 2), 1)
    assert data["bmi_category"] == "正常"


@pytest.mark.asyncio
async def test_profile_accepts_privacy_preserving_gender_value(client):
    token = await register_and_get_token(client, "private-gender@example.com")

    response = await client.put(
        "/api/v1/profile",
        json={"gender": "prefer_not_to_say"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["gender"] == "prefer_not_to_say"


@pytest.mark.asyncio
async def test_profile_rejects_unknown_gender_value(client):
    token = await register_and_get_token(client, "invalid-gender@example.com")

    response = await client.put(
        "/api/v1/profile",
        json={"gender": "unknown"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_log_weight_updates_bmi(client):
    token = await register_and_get_token(client, "weight@example.com")
    await client.put("/api/v1/profile", json={"height_cm": 170},
                     headers={"Authorization": f"Bearer {token}"})
    resp = await client.post("/api/v1/profile/weight", json={"weight_kg": 75},
                              headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    profile_resp = await client.get("/api/v1/profile",
                                     headers={"Authorization": f"Bearer {token}"})
    assert profile_resp.json()["bmi_category"] == "超重"


@pytest.mark.asyncio
async def test_get_weight_history(client):
    token = await register_and_get_token(client, "weight-history@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    await client.post(
        "/api/v1/profile/weight", json={"weight_kg": 72.5}, headers=headers
    )
    await client.post(
        "/api/v1/profile/weight", json={"weight_kg": 71.8}, headers=headers
    )

    resp = await client.get("/api/v1/profile/weight", headers=headers)

    assert resp.status_code == 200
    history = resp.json()
    assert [row["weight_kg"] for row in history] == [72.5, 71.8]
    assert all("recorded_at" in row for row in history)


@pytest.mark.asyncio
async def test_weight_history_isolated_between_users(client):
    first_token = await register_and_get_token(client, "weight-owner@example.com")
    second_token = await register_and_get_token(client, "weight-other@example.com")
    await client.post(
        "/api/v1/profile/weight",
        json={"weight_kg": 80},
        headers={"Authorization": f"Bearer {first_token}"},
    )

    resp = await client.get(
        "/api/v1/profile/weight",
        headers={"Authorization": f"Bearer {second_token}"},
    )

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_update_onboarding_completed(client):
    token = await register_and_get_token(client, "onboard@example.com")
    resp = await client.put("/api/v1/profile", json={
        "age": 25, "gender": "female", "height_cm": 165, "weight_kg": 58,
        "experience_level": "1-2年", "primary_goal": "减脂",
        "training_days_per_week": 4, "session_duration_min": 60,
        "training_location": "商业健身房",
        "diet_restriction": "lactose_free",
        "injuries": ["膝关节"],
        "chronic_conditions": ["高血压"],
        "onboarding_completed": True
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["onboarding_completed"] is True
    assert data["diet_restriction"] == "lactose_free"
    assert data["injuries"] == ["膝关节"]
    assert data["chronic_conditions"] == ["高血压"]


@pytest.mark.asyncio
async def test_cannot_complete_onboarding_with_missing_safety_profile(client):
    token = await register_and_get_token(client, "incomplete-onboard@example.com")

    response = await client.put(
        "/api/v1/profile",
        json={"onboarding_completed": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    assert "完成引导前请填写" in response.json()["detail"]
