from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services.daily_meal_optimizer import (
    DailyMealOptimizationError,
    nutrition_fit,
    optimize_daily_meal_amounts,
)
from app.services import daily_meal_optimizer as optimizer_module


STANDARD_FOODS = [
    {
        "id": "rice",
        "name": "米饭",
        "category": "碳水",
        "calories_per_100g": 130,
        "protein_g": 3,
        "carbs_g": 28,
        "fat_g": 1,
        "common_portion_g": 150,
    },
    {
        "id": "chicken",
        "name": "鸡胸肉",
        "category": "蛋白质",
        "calories_per_100g": 165,
        "protein_g": 31,
        "carbs_g": 0,
        "fat_g": 3.6,
        "common_portion_g": 150,
    },
    {
        "id": "oil",
        "name": "橄榄油",
        "category": "油脂",
        "calories_per_100g": 884,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 100,
        "common_portion_g": 14,
    },
]
STANDARD_TARGETS = {
    "calories_kcal": {"min": 1800, "max": 2200},
    "protein_g": {"min": 105, "max": 135},
}
EMPTY_TOTALS = {
    "calories": 0,
    "protein_g": 0,
    "carbs_g": 0,
    "fat_g": 0,
}


def _standard_meals() -> list[dict]:
    return [
        {
            "meal_type": meal_type,
            "items": [
                {"food_id": "rice", "amount_g": 200},
                {"food_id": "chicken", "amount_g": 120},
                {"food_id": "oil", "amount_g": 10},
            ],
        }
        for meal_type in ("早餐", "午餐", "晚餐")
    ]


def _totals(meals: list[dict], foods: list[dict], existing=None) -> dict[str, float]:
    food_map = {item["id"]: item for item in foods}
    totals = dict(existing or EMPTY_TOTALS)
    for meal in meals:
        for item in meal["items"]:
            food = food_map[item["food_id"]]
            factor = item["amount_g"] / 100
            totals["calories"] += food["calories_per_100g"] * factor
            totals["protein_g"] += food["protein_g"] * factor
            totals["carbs_g"] += food["carbs_g"] * factor
            totals["fat_g"] += food["fat_g"] * factor
    return {key: round(value, 1) for key, value in totals.items()}


def test_optimizer_hits_ideal_ranges_with_integer_serving_steps():
    optimized = optimize_daily_meal_amounts(
        meals=_standard_meals(),
        food_candidates=STANDARD_FOODS,
        existing_totals=EMPTY_TOTALS,
        targets=STANDARD_TARGETS,
        mode="ideal",
    )

    totals = _totals(optimized.meals, STANDARD_FOODS)
    fit = nutrition_fit(totals, STANDARD_TARGETS)
    assert fit["status"] == "within_target"
    assert fit["deviations"] == []
    for meal in optimized.meals:
        for item in meal["items"]:
            if item["food_id"] == "oil":
                assert item["amount_g"] == int(item["amount_g"])
                assert 1 <= item["amount_g"] <= 50
            else:
                assert item["amount_g"] % 5 == 0
                assert 5 <= item["amount_g"] <= 500


def test_optimizer_uses_existing_meals_as_fixed_baseline():
    existing = {
        "calories": 420,
        "protein_g": 28,
        "carbs_g": 45,
        "fat_g": 14,
    }
    optimized = optimize_daily_meal_amounts(
        meals=_standard_meals(),
        food_candidates=STANDARD_FOODS,
        existing_totals=existing,
        targets=STANDARD_TARGETS,
        mode="ideal",
    )
    fit = nutrition_fit(
        _totals(optimized.meals, STANDARD_FOODS, existing),
        STANDARD_TARGETS,
    )
    assert fit["status"] == "within_target"


def test_acceptable_mode_returns_visible_balanced_deviation():
    foods = [{
        "id": "balanced-near",
        "name": "接近目标食品",
        "category": "混合",
        "calories_per_100g": 100,
        "protein_g": 5,
        "carbs_g": 17,
        "fat_g": 1.8,
        "common_portion_g": 300,
    }]
    meals = [
        {
            "meal_type": meal_type,
            "items": [{"food_id": "balanced-near", "amount_g": 330}],
        }
        for meal_type in ("早餐", "午餐", "晚餐")
    ]
    targets = {
        "calories_kcal": {"min": 900, "max": 1100},
        "protein_g": {"min": 45, "max": 55},
    }

    with pytest.raises(DailyMealOptimizationError) as ideal_error:
        optimize_daily_meal_amounts(
            meals=meals,
            food_candidates=foods,
            existing_totals=EMPTY_TOTALS,
            targets=targets,
            mode="ideal",
        )
    assert ideal_error.value.code == "daily_meal_optimization_infeasible"

    optimized = optimize_daily_meal_amounts(
        meals=meals,
        food_candidates=foods,
        existing_totals=EMPTY_TOTALS,
        targets=targets,
        mode="acceptable",
    )
    fit = nutrition_fit(_totals(optimized.meals, foods), targets)
    assert fit["status"] == "acceptable_deviation"
    assert {item["metric"] for item in fit["deviations"]} == {
        "fat_energy_percent",
        "carb_energy_percent",
    }


def test_optimizer_rejects_composition_outside_balanced_tolerance():
    foods = [{
        "id": "sugar",
        "name": "纯碳水测试食品",
        "category": "碳水",
        "calories_per_100g": 100,
        "protein_g": 0,
        "carbs_g": 25,
        "fat_g": 0,
        "common_portion_g": 300,
    }]
    meals = [{
        "meal_type": "早餐",
        "items": [{"food_id": "sugar", "amount_g": 300}],
    }]
    targets = {
        "calories_kcal": {"min": 250, "max": 350},
        "protein_g": {"min": 0, "max": 20},
    }

    with pytest.raises(DailyMealOptimizationError) as error:
        optimize_daily_meal_amounts(
            meals=meals,
            food_candidates=foods,
            existing_totals=EMPTY_TOTALS,
            targets=targets,
            mode="acceptable",
        )
    assert error.value.code == "daily_meal_optimization_infeasible"


def test_optimizer_is_deterministic_for_identical_input():
    values = [
        optimize_daily_meal_amounts(
            meals=_standard_meals(),
            food_candidates=STANDARD_FOODS,
            existing_totals=EMPTY_TOTALS,
            targets=STANDARD_TARGETS,
            mode="ideal",
        ).meals
        for _ in range(3)
    ]
    assert values[0] == values[1] == values[2]


def test_optimizer_failure_does_not_fall_back_to_unverified_amounts():
    with patch(
        "app.services.daily_meal_optimizer.milp",
        side_effect=RuntimeError("solver unavailable"),
    ):
        with pytest.raises(DailyMealOptimizationError) as error:
            optimize_daily_meal_amounts(
                meals=_standard_meals(),
                food_candidates=STANDARD_FOODS,
                existing_totals=EMPTY_TOTALS,
                targets=STANDARD_TARGETS,
                mode="ideal",
            )
    assert error.value.code == "daily_meal_optimizer_unavailable"


def test_optimizer_timeout_does_not_fall_back_to_unverified_amounts():
    timed_out = SimpleNamespace(success=False, status=1, x=None)
    with patch(
        "app.services.daily_meal_optimizer.milp",
        return_value=timed_out,
    ):
        with pytest.raises(DailyMealOptimizationError) as error:
            optimize_daily_meal_amounts(
                meals=_standard_meals(),
                food_candidates=STANDARD_FOODS,
                existing_totals=EMPTY_TOTALS,
                targets=STANDARD_TARGETS,
                mode="ideal",
            )
    assert error.value.code == "daily_meal_optimizer_timeout_no_solution"


def test_optimizer_accepts_time_limited_feasible_incumbent_after_validation():
    real_milp = optimizer_module.milp

    def time_limited_with_incumbent(*args, **kwargs):
        result = real_milp(*args, **kwargs)
        result.success = False
        result.status = 1
        return result

    with patch(
        "app.services.daily_meal_optimizer.milp",
        side_effect=time_limited_with_incumbent,
    ):
        optimized = optimize_daily_meal_amounts(
            meals=_standard_meals(),
            food_candidates=STANDARD_FOODS,
            existing_totals=EMPTY_TOTALS,
            targets=STANDARD_TARGETS,
            mode="ideal",
        )

    assert optimized.solver_status == "feasible_incumbent"
    assert nutrition_fit(
        _totals(optimized.meals, STANDARD_FOODS), STANDARD_TARGETS
    )["status"] == "within_target"


def test_optimizer_rejects_invalid_time_limited_incumbent():
    invalid = SimpleNamespace(
        success=False,
        status=1,
        x=[0.5],
        fun=0.0,
    )
    with patch(
        "app.services.daily_meal_optimizer.milp",
        return_value=invalid,
    ):
        with pytest.raises(DailyMealOptimizationError) as error:
            optimize_daily_meal_amounts(
                meals=_standard_meals(),
                food_candidates=STANDARD_FOODS,
                existing_totals=EMPTY_TOTALS,
                targets=STANDARD_TARGETS,
                mode="ideal",
            )
    assert error.value.code == "daily_meal_optimizer_unavailable"
