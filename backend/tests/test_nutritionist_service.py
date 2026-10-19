import pytest
from datetime import date
from unittest.mock import patch, AsyncMock
from app.models.user import User
from app.models.meal import MealLog, MealItem
from app.services.nutritionist import get_daily_nutrition_advice
import bcrypt


def _make_user(uid: str, email: str) -> User:
    pw = bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode()
    return User(id=uid, email=email, password_hash=pw)


@pytest.mark.asyncio
async def test_advice_no_meals(db_session):
    db_session.add(_make_user("nut-u1", "nut1@example.com"))
    await db_session.commit()

    with patch(
        "app.services.nutritionist.chat_completion",
        new=AsyncMock(return_value="建议增加蛋白质摄入。"),
    ):
        advice = await get_daily_nutrition_advice(db_session, user_id="nut-u1")

    assert "建议" in advice


@pytest.mark.asyncio
async def test_advice_with_meals(db_session):
    db_session.add(_make_user("nut-u2", "nut2@example.com"))
    await db_session.commit()

    meal = MealLog(user_id="nut-u2", logged_at=date.today(), meal_type="午餐")
    db_session.add(meal)
    await db_session.flush()

    db_session.add(MealItem(
        meal_id=meal.id,
        food_name="鸡胸肉",
        amount_g=150.0,
        calories=165.0,
        protein_g=31.0,
        carbs_g=0.0,
        fat_g=3.6,
    ))
    await db_session.commit()

    with patch(
        "app.services.nutritionist.chat_completion",
        new=AsyncMock(return_value="今日蛋白质摄入充足，建议补充蔬菜。"),
    ):
        advice = await get_daily_nutrition_advice(db_session, user_id="nut-u2")

    assert isinstance(advice, str)
    assert len(advice) > 0


@pytest.mark.asyncio
async def test_advice_endpoint(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "nut3@example.com", "password": "pass1234"},
    )
    token = resp.json()["access_token"]

    with patch(
        "app.services.nutritionist.chat_completion",
        new=AsyncMock(return_value="营养均衡，继续保持。"),
    ):
        resp = await client.get(
            "/api/v1/meals/advice",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert "advice" in resp.json()
    assert len(resp.json()["advice"]) > 0
