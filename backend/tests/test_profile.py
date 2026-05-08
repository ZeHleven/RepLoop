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
async def test_log_weight_updates_bmi(client):
    token = await register_and_get_token(client, "weight@example.com")
    await client.put("/api/v1/profile", json={"height_cm": 170},
                     headers={"Authorization": f"Bearer {token}"})
    resp = await client.post("/api/v1/profile/weight", json={"weight_kg": 85},
                              headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    profile_resp = await client.get("/api/v1/profile",
                                     headers={"Authorization": f"Bearer {token}"})
    assert profile_resp.json()["bmi_category"] == "超重"


@pytest.mark.asyncio
async def test_update_onboarding_completed(client):
    token = await register_and_get_token(client, "onboard@example.com")
    resp = await client.put("/api/v1/profile", json={
        "age": 25, "gender": "female", "height_cm": 165, "weight_kg": 58,
        "experience_level": "1-2年", "primary_goal": "减脂",
        "training_days_per_week": 4, "session_duration_min": 60,
        "training_location": "商业健身房",
        "onboarding_completed": True
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["onboarding_completed"] is True
