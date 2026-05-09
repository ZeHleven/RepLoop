from datetime import date
from app.models.meal import MealLog, MealItem


def test_meal_log_instantiation():
    m = MealLog(user_id="u1", logged_at=date.today(), meal_type="早餐")
    assert m.id is not None
    assert m.meal_type == "早餐"


def test_meal_item_instantiation():
    item = MealItem(meal_id="m1", food_name="鸡胸肉", amount_g=150.0, calories=165.0)
    assert item.id is not None
    assert item.food_name == "鸡胸肉"
    assert item.amount_g == 150.0
