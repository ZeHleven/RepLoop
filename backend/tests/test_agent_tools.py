from app.services.business_clock import business_today
import bcrypt
import pytest

from app.models.food import Food
from app.models.meal import MealItem, MealLog
from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from app.services.agent_tools import build_read_tools


def _user(user_id: str, email: str) -> User:
    return User(
        id=user_id,
        email=email,
        password_hash=bcrypt.hashpw(b"testpass", bcrypt.gensalt()).decode(),
    )


@pytest.mark.asyncio
async def test_profile_and_health_tools_use_server_injected_user(db_session):
    db_session.add(_user("tool-user-1", "tool-user-1@example.com"))
    await db_session.commit()
    db_session.add(UserProfile(
        user_id="tool-user-1",
        age=30,
        primary_goal="增肌",
        training_location="健身房",
        injuries=["膝关节"],
        chronic_conditions=[],
        onboarding_completed=True,
    ))
    await db_session.commit()

    profile_tool, health_tool = build_read_tools(
        db_session,
        user_id="tool-user-1",
        allowlist=[
            "profile.get_summary",
            "health.get_screening_summary",
        ],
    )

    profile = await profile_tool.ainvoke({})
    health = await health_tool.ainvoke({})

    assert profile["primary_goal"] == "增肌"
    assert "injuries" not in profile
    assert health["injuries"] == ["膝关节"]
    assert "primary_goal" not in health
    assert "user_id" not in str(profile_tool.args_schema.model_json_schema())
    assert "user_id" not in str(health_tool.args_schema.model_json_schema())


def test_tool_builder_fails_closed_for_unknown_or_write_tool(db_session):
    with pytest.raises(ValueError, match="Unknown or non-read"):
        build_read_tools(
            db_session,
            user_id="tool-user-1",
            allowlist=["workout.complete"],
        )


def test_tool_descriptions_are_specific_and_nonempty(db_session):
    tools = build_read_tools(
        db_session,
        user_id="tool-user-1",
        allowlist=[
            "plan.get_active",
            "workout.get_next",
            "workout.list_history",
            "workout.get_progress",
        ],
    )

    assert len({item.name for item in tools}) == len(tools)
    assert all("示例" in item.description for item in tools)
    assert "整个当前计划" in tools[0].description
    assert "下一次计划训练日" in tools[1].description


def test_every_read_tool_schema_excludes_identity_fields(db_session):
    from app.services.agent_tools import READ_TOOL_IDS

    allowlist = list(READ_TOOL_IDS)

    tools = build_read_tools(
        db_session,
        user_id="server-owned-user-id",
        allowlist=allowlist,
    )

    assert len(tools) == 14
    assert all(
        "user_id" not in str(item.args_schema.model_json_schema())
        for item in tools
    )


@pytest.mark.asyncio
async def test_weight_and_nutrition_tools_read_only_current_user(db_session):
    from datetime import date

    db_session.add(_user("tool-nutrition-user", "tool-nutrition@example.com"))
    db_session.add(_user("tool-other-user", "tool-other@example.com"))
    await db_session.commit()
    db_session.add_all([
        WeightLog(user_id="tool-nutrition-user", weight_kg=65),
        WeightLog(user_id="tool-other-user", weight_kg=99),
    ])
    food = Food(
        id="tool-food-chicken",
        name_zh="鸡胸肉",
        category="肉类",
        calories_per_100g=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
        is_active=True,
    )
    db_session.add(food)
    meal = MealLog(
        user_id="tool-nutrition-user",
        logged_at=business_today(),
        meal_type="午餐",
    )
    db_session.add(meal)
    await db_session.flush()
    db_session.add(MealItem(
        meal_id=meal.id,
        food_id=food.id,
        food_name=food.name_zh,
        amount_g=100,
        calories=165,
        protein_g=31,
        carbs_g=0,
        fat_g=3.6,
    ))
    await db_session.commit()

    weight_tool, today_tool, history_tool, food_tool = build_read_tools(
        db_session,
        user_id="tool-nutrition-user",
        allowlist=[
            "weight.list_history",
            "nutrition.get_today",
            "nutrition.list_history",
            "food.search",
        ],
    )
    weight = await weight_tool.ainvoke({"limit": 30})
    today = await today_tool.ainvoke({})
    history = await history_tool.ainvoke({"days": 30})
    foods = await food_tool.ainvoke({"query": "鸡胸肉", "limit": 10})

    assert [item["weight_kg"] for item in weight["records"]] == [65]
    assert today["count"] == 1
    assert today["total_calories"] == 165
    assert history["count"] == 1
    assert food.id in {item["id"] for item in foods["foods"]}
