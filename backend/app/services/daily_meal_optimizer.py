from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp


SOLVER_VERSION = "highs_milp_v3"
IDEAL_FAT_RANGE = (20.0, 35.0)
IDEAL_CARB_RANGE = (45.0, 65.0)
ACCEPTABLE_FAT_RANGE = (15.0, 40.0)
ACCEPTABLE_CARB_RANGE = (40.0, 70.0)


class DailyMealOptimizationError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        violated_metrics: tuple[str, ...] = (),
        duration_ms: int = 0,
        constraint_violations: tuple[dict[str, Any], ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.violated_metrics = violated_metrics
        self.duration_ms = duration_ms
        self.constraint_violations = constraint_violations


@dataclass(frozen=True)
class OptimizedMealDraft:
    meals: list[dict[str, Any]]
    status: Literal["within_target", "acceptable_deviation"]
    duration_ms: int
    objective_value: float
    nutrition_score: float
    portion_score: float
    solver_status: Literal["optimal", "feasible_incumbent"] = "optimal"


@dataclass(frozen=True)
class _Occurrence:
    food_id: str
    locations: tuple[tuple[int, int], ...]
    location_suggestions: tuple[float, ...]
    step_g: float
    minimum_units: int
    maximum_units: int
    suggested_units: float
    preferred_units: float
    calories_per_unit: float
    protein_per_unit: float
    carbs_per_unit: float
    fat_per_unit: float


def _is_oil(food: dict[str, Any]) -> bool:
    name = str(food.get("name") or food.get("food_name") or "")
    category = str(food.get("category") or "").strip().lower()
    return "油" in name or category in {"oil", "fats", "油脂"}


def acceptable_targets(targets: dict[str, Any]) -> dict[str, tuple[float, float]]:
    calories = targets["calories_kcal"]
    protein = targets["protein_g"]
    return {
        "calories": (float(calories["min"]) * 0.9, float(calories["max"]) * 1.1),
        "protein_g": (float(protein["min"]) * 0.9, float(protein["max"]) * 1.1),
        "fat_energy_percent": ACCEPTABLE_FAT_RANGE,
        "carb_energy_percent": ACCEPTABLE_CARB_RANGE,
    }


def nutrition_metrics(totals: dict[str, float]) -> dict[str, float]:
    calories = float(totals["calories"])
    fat_percent = 0.0 if calories <= 0 else float(totals["fat_g"]) * 9 / calories * 100
    carb_percent = 0.0 if calories <= 0 else float(totals["carbs_g"]) * 4 / calories * 100
    return {
        "calories": calories,
        "protein_g": float(totals["protein_g"]),
        "fat_energy_percent": fat_percent,
        "carb_energy_percent": carb_percent,
    }


def _ideal_ranges(targets: dict[str, Any]) -> dict[str, tuple[float, float]]:
    return {
        "calories": (
            float(targets["calories_kcal"]["min"]),
            float(targets["calories_kcal"]["max"]),
        ),
        "protein_g": (
            float(targets["protein_g"]["min"]),
            float(targets["protein_g"]["max"]),
        ),
        "fat_energy_percent": IDEAL_FAT_RANGE,
        "carb_energy_percent": IDEAL_CARB_RANGE,
    }


def _outside_distance(value: float, limits: tuple[float, float]) -> float:
    if value < limits[0]:
        return limits[0] - value
    if value > limits[1]:
        return value - limits[1]
    return 0.0


def nutrition_fit(
    totals: dict[str, float],
    targets: dict[str, Any],
    *,
    solver_version: str = SOLVER_VERSION,
) -> dict[str, Any]:
    metrics = nutrition_metrics(totals)
    ideal = _ideal_ranges(targets)
    acceptable = acceptable_targets(targets)
    labels = {
        "calories": ("热量", "kcal"),
        "protein_g": ("蛋白质", "g"),
        "fat_energy_percent": ("脂肪供能比", "%"),
        "carb_energy_percent": ("碳水供能比", "%"),
    }
    violations = tuple(
        metric for metric, value in metrics.items()
        if _outside_distance(value, acceptable[metric]) > 1e-6
    )
    if violations:
        raise DailyMealOptimizationError(
            "daily_meal_optimization_infeasible",
            "当前食品组合无法在允许偏差内满足全天营养目标",
            violated_metrics=violations,
        )

    deviations: list[dict[str, Any]] = []
    normalized_score = 0.0
    for metric, value in metrics.items():
        distance = _outside_distance(value, ideal[metric])
        if distance <= 1e-6:
            continue
        lower, upper = ideal[metric]
        acceptable_lower, acceptable_upper = acceptable[metric]
        direction = "below" if value < lower else "above"
        allowance = (
            lower - acceptable_lower
            if direction == "below"
            else acceptable_upper - upper
        )
        normalized_score += distance / max(allowance, 1e-9)
        label, unit = labels[metric]
        deviations.append({
            "metric": metric,
            "label": label,
            "actual": round(value, 1),
            "ideal_min": round(lower, 1),
            "ideal_max": round(upper, 1),
            "acceptable_min": round(acceptable_lower, 1),
            "acceptable_max": round(acceptable_upper, 1),
            "direction": direction,
            "amount": round(distance, 1),
            "unit": unit,
        })
    return {
        "status": "acceptable_deviation" if deviations else "within_target",
        "deviations": deviations,
        "solver_version": solver_version,
        "nutrition_score": round(normalized_score, 6),
    }


def target_gaps(totals: dict[str, float], targets: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = nutrition_metrics(totals)
    ideal = _ideal_ranges(targets)
    gaps: list[dict[str, Any]] = []
    for metric, value in metrics.items():
        distance = _outside_distance(value, ideal[metric])
        if distance <= 1e-6:
            continue
        gaps.append({
            "metric": metric,
            "actual": round(value, 1),
            "target_min": round(ideal[metric][0], 1),
            "target_max": round(ideal[metric][1], 1),
            "direction": "below" if value < ideal[metric][0] else "above",
            "gap": round(distance, 1),
        })
    return gaps


def _occurrences(
    meals: list[dict[str, Any]],
    food_candidates: list[dict[str, Any]],
) -> list[_Occurrence]:
    foods = {str(item["id"]): item for item in food_candidates}
    occurrences: list[_Occurrence] = []
    conflicts: list[dict[str, Any]] = []
    for meal_index, meal in enumerate(meals):
        substantial = False
        for item_index, item in enumerate(meal["items"]):
            food_id = str(item["food_id"])
            food = foods.get(food_id)
            if food is None:
                raise DailyMealOptimizationError("daily_meal_food_validation_failed", "方案包含食品候选范围之外的项目")
            oil = _is_oil(food)
            step = 1.0 if oil else 5.0
            minimum_g, maximum_g = (1.0, 50.0) if oil else (25.0, 500.0)
            suggested_g = float(item["amount_g"])
            if not math.isfinite(suggested_g) or suggested_g <= 0:
                raise DailyMealOptimizationError("daily_meal_food_validation_failed", "食品份量无效")
            lower = math.ceil(max(minimum_g, suggested_g * 0.5) / step)
            upper = math.floor(min(maximum_g, suggested_g * 1.5) / step)
            if lower > upper:
                conflicts.append({
                    "metric": "portion_bounds", "meal_index": meal_index,
                    "item_index": item_index, "meal_type": meal["meal_type"],
                    "food_id": food_id, "food_name": food.get("name_zh", ""),
                    "suggested_g": suggested_g, "minimum_g": minimum_g,
                    "maximum_g": maximum_g, "step_g": step,
                    "relative_min": 0.5, "relative_max": 1.5,
                    "feasible_min_g": lower * step, "feasible_max_g": upper * step,
                })
                substantial |= not oil
                continue
            substantial |= not oil
            common_g = float(food.get("common_portion_g") or suggested_g)
            occurrences.append(_Occurrence(
                food_id=food_id,
                locations=((meal_index, item_index),),
                location_suggestions=(suggested_g / step,),
                step_g=step,
                minimum_units=lower,
                maximum_units=upper,
                suggested_units=suggested_g / step,
                preferred_units=max(lower, min(upper, common_g / step)),
                calories_per_unit=float(food["calories_per_100g"]) * step / 100,
                protein_per_unit=float(food["protein_g"]) * step / 100,
                carbs_per_unit=float(food["carbs_g"]) * step / 100,
                fat_per_unit=float(food["fat_g"]) * step / 100,
            ))
        if meal["meal_type"] in {"早餐", "午餐", "晚餐"} and not substantial:
            raise DailyMealOptimizationError("daily_meal_optimization_infeasible", "主餐缺少实质食品", violated_metrics=("main_without_substantial_food",))
    if conflicts:
        raise DailyMealOptimizationError(
            "daily_meal_optimization_infeasible", "原稿份量无法在可用范围内调整",
            violated_metrics=("portion_bounds",), constraint_violations=tuple(conflicts),
        )
    if not occurrences:
        raise DailyMealOptimizationError(
            "daily_meal_food_validation_failed",
            "方案没有可用于配平的食品",
        )
    return occurrences


def _add_upper(
    rows: list[list[float]],
    limits: list[float],
    coefficients: list[float],
    limit: float,
) -> None:
    rows.append(coefficients)
    limits.append(limit)


def _solve(
    *,
    meals: list[dict[str, Any]],
    food_candidates: list[dict[str, Any]],
    existing_totals: dict[str, float],
    targets: dict[str, Any],
    mode: Literal["ideal", "acceptable"],
    time_limit_seconds: float,
) -> OptimizedMealDraft:
    started = time.perf_counter()
    occurrences = _occurrences(meals, food_candidates)
    count = len(occurrences)
    metric_count = 4
    variable_count = count * 3 + metric_count
    x_offset = 0
    suggestion_deviation_offset = count
    preferred_deviation_offset = count * 2
    metric_offset = count * 3

    objective = np.zeros(variable_count, dtype=float)
    for index, occurrence in enumerate(occurrences):
        deviation_scale = max(
            occurrence.suggested_units,
            occurrence.preferred_units,
            1.0,
        )
        objective[suggestion_deviation_offset + index] = 0.5 / deviation_scale
        objective[preferred_deviation_offset + index] = 0.5 / deviation_scale
        objective[x_offset + index] = (index + 1) * 1e-8
    if mode == "acceptable":
        objective[metric_offset:] = 1000.0

    lower_bounds = np.zeros(variable_count, dtype=float)
    upper_bounds = np.full(variable_count, np.inf, dtype=float)
    integrality = np.zeros(variable_count, dtype=int)
    for index, occurrence in enumerate(occurrences):
        lower_bounds[index] = occurrence.minimum_units
        upper_bounds[index] = occurrence.maximum_units
        integrality[index] = 1

    rows: list[list[float]] = []
    limits: list[float] = []
    for index, occurrence in enumerate(occurrences):
        row = [0.0] * variable_count
        row[index] = 1.0
        row[suggestion_deviation_offset + index] = -1.0
        _add_upper(rows, limits, row, occurrence.suggested_units)
        row = [0.0] * variable_count
        row[index] = -1.0
        row[suggestion_deviation_offset + index] = -1.0
        _add_upper(rows, limits, row, -occurrence.suggested_units)
        row = [0.0] * variable_count
        row[index] = 1.0
        row[preferred_deviation_offset + index] = -1.0
        _add_upper(rows, limits, row, occurrence.preferred_units)
        row = [0.0] * variable_count
        row[index] = -1.0
        row[preferred_deviation_offset + index] = -1.0
        _add_upper(rows, limits, row, -occurrence.preferred_units)

    calories = [item.calories_per_unit for item in occurrences]
    protein = [item.protein_per_unit for item in occurrences]
    carbs = [item.carbs_per_unit for item in occurrences]
    fat = [item.fat_per_unit for item in occurrences]
    # Meal-local variables preserve the model's distribution across meals.
    # Shares concern only generated meals; recorded food remains fixed below.
    mains = [i for i, meal in enumerate(meals) if meal["meal_type"] in {"早餐", "午餐", "晚餐"}]
    share_bounds = {2: (0.25, 0.75), 3: (0.15, 0.50)}.get(len(mains))
    if share_bounds:
        low, high = share_bounds
        for meal_index in mains:
            local = [calories[i] if occurrence.locations[0][0] == meal_index else 0.0 for i, occurrence in enumerate(occurrences)]
            _add_upper(rows, limits, [local[i] - high * calories[i] for i in range(count)] + [0.0] * (variable_count - count), 0.0)
            _add_upper(rows, limits, [low * calories[i] - local[i] for i in range(count)] + [0.0] * (variable_count - count), 0.0)
    if mains:
        snack = [calories[i] if occurrence.locations[0][0] not in mains else 0.0 for i, occurrence in enumerate(occurrences)]
        _add_upper(rows, limits, [snack[i] - 0.15 * calories[i] for i in range(count)] + [0.0] * (variable_count - count), 0.0)
    fixed_calories = float(existing_totals.get("calories", 0))
    fixed_protein = float(existing_totals.get("protein_g", 0))
    fixed_carbs = float(existing_totals.get("carbs_g", 0))
    fixed_fat = float(existing_totals.get("fat_g", 0))

    if mode == "ideal":
        ranges = _ideal_ranges(targets)
    else:
        ranges = acceptable_targets(targets)

    def add_range(coefficients: list[float], fixed: float, limits_pair: tuple[float, float]) -> None:
        upper_row = coefficients + [0.0] * (variable_count - count)
        _add_upper(rows, limits, upper_row, limits_pair[1] - fixed)
        lower_row = [-value for value in coefficients] + [0.0] * (variable_count - count)
        _add_upper(rows, limits, lower_row, fixed - limits_pair[0])

    add_range(calories, fixed_calories, ranges["calories"])
    add_range(protein, fixed_protein, ranges["protein_g"])

    def add_ratio_range(
        nutrient: list[float],
        fixed_nutrient: float,
        multiplier: float,
        ratio_range: tuple[float, float],
    ) -> None:
        low = ratio_range[0] / 100
        high = ratio_range[1] / 100
        lower_expression = [
            low * calories[index] - multiplier * nutrient[index]
            for index in range(count)
        ] + [0.0] * (variable_count - count)
        _add_upper(
            rows,
            limits,
            lower_expression,
            multiplier * fixed_nutrient - low * fixed_calories,
        )
        upper_expression = [
            multiplier * nutrient[index] - high * calories[index]
            for index in range(count)
        ] + [0.0] * (variable_count - count)
        _add_upper(
            rows,
            limits,
            upper_expression,
            high * fixed_calories - multiplier * fixed_nutrient,
        )

    add_ratio_range(fat, fixed_fat, 9.0, ranges["fat_energy_percent"])
    add_ratio_range(carbs, fixed_carbs, 4.0, ranges["carb_energy_percent"])

    if mode == "acceptable":
        ideal = _ideal_ranges(targets)
        target_calories = sum(ideal["calories"]) / 2
        target_protein = sum(ideal["protein_g"]) / 2
        scale_calories = max(target_calories, 1.0)
        midpoint_specs = (
            (calories, fixed_calories, target_calories, scale_calories),
            (protein, fixed_protein, target_protein, max(target_protein, 1.0)),
            (
                [
                    9 * value - 0.275 * calories[index]
                    for index, value in enumerate(fat)
                ],
                9 * fixed_fat - 0.275 * fixed_calories,
                0.0,
                scale_calories,
            ),
            (
                [
                    4 * value - 0.55 * calories[index]
                    for index, value in enumerate(carbs)
                ],
                4 * fixed_carbs - 0.55 * fixed_calories,
                0.0,
                scale_calories,
            ),
        )
        for metric_index, (coefficients, fixed, midpoint, scale) in enumerate(
            midpoint_specs
        ):
            q_index = metric_offset + metric_index
            row = coefficients + [0.0] * (variable_count - count)
            row[q_index] = -scale
            _add_upper(rows, limits, row, midpoint - fixed)
            row = [-value for value in coefficients] + [
                0.0
            ] * (variable_count - count)
            row[q_index] = -scale
            _add_upper(rows, limits, row, fixed - midpoint)

    try:
        result = milp(
            c=objective,
            integrality=integrality,
            bounds=Bounds(lower_bounds, upper_bounds),
            constraints=LinearConstraint(
                np.asarray(rows, dtype=float),
                np.full(len(rows), -np.inf, dtype=float),
                np.asarray(limits, dtype=float),
            ),
            options={
                "presolve": True,
                "time_limit": time_limit_seconds,
                "mip_rel_gap": 0.001,
            },
        )
    except Exception as exc:
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        raise DailyMealOptimizationError(
            "daily_meal_optimizer_unavailable",
            "营养配平服务暂时不可用",
            duration_ms=duration_ms,
        ) from exc
    duration_ms = max(0, round((time.perf_counter() - started) * 1000))
    solver_status = int(result.status)
    if solver_status == 2:
        raise DailyMealOptimizationError(
            "daily_meal_optimization_infeasible",
            "当前食品组合无法满足营养目标",
            duration_ms=duration_ms,
        )
    if result.x is None:
        code = (
            "daily_meal_optimizer_timeout_no_solution"
            if solver_status == 1
            else "daily_meal_optimizer_unavailable"
        )
        raise DailyMealOptimizationError(
            code,
            (
                "当前食品组合未在时限内找到可行解"
                if code == "daily_meal_optimizer_timeout_no_solution"
                else "营养配平服务暂时不可用"
            ),
            duration_ms=duration_ms,
        )
    if solver_status not in {0, 1}:
        raise DailyMealOptimizationError(
            "daily_meal_optimizer_unavailable",
            "营养配平服务返回了无法采用的状态",
            duration_ms=duration_ms,
        )

    solution = np.asarray(result.x, dtype=float)
    if (
        solution.shape != (variable_count,)
        or not np.all(np.isfinite(solution))
        or np.any(solution < lower_bounds - 1e-6)
        or np.any(solution > upper_bounds + 1e-6)
        or np.any(np.abs(solution[:count] - np.rint(solution[:count])) > 1e-6)
        or np.any(np.asarray(rows, dtype=float) @ solution > np.asarray(limits) + 1e-5)
    ):
        raise DailyMealOptimizationError(
            "daily_meal_optimizer_unavailable",
            "营养配平服务返回了未通过约束复验的结果",
            duration_ms=duration_ms,
        )

    optimized = [
        {
            "meal_type": str(meal["meal_type"]),
            "items": [
                {"food_id": str(item["food_id"]), "amount_g": float(item["amount_g"])}
                for item in meal["items"]
            ],
        }
        for meal in meals
    ]
    portion_score = 0.0
    for index, occurrence in enumerate(occurrences):
        units = int(round(float(solution[index])))
        meal_index, item_index = occurrence.locations[0]
        optimized[meal_index]["items"][item_index]["amount_g"] = units * occurrence.step_g
        deviation_scale = max(
            occurrence.suggested_units,
            occurrence.preferred_units,
            1.0,
        )
        portion_score += 0.5 * abs(
            units - occurrence.suggested_units
        ) / deviation_scale
        portion_score += 0.5 * abs(
            units - occurrence.preferred_units
        ) / deviation_scale

    validate_meal_usability(optimized, meals, food_candidates)
    return OptimizedMealDraft(
        meals=optimized,
        status="within_target" if mode == "ideal" else "acceptable_deviation",
        duration_ms=duration_ms,
        objective_value=float(
            result.fun
            if getattr(result, "fun", None) is not None
            else objective @ solution
        ),
        nutrition_score=(
            float(sum(solution[metric_offset:]))
            if mode == "acceptable"
            else 0.0
        ),
        portion_score=portion_score,
        solver_status=(
            "feasible_incumbent" if solver_status == 1 else "optimal"
        ),
    )


def optimize_daily_meal_amounts(
    *,
    meals: list[dict[str, Any]],
    food_candidates: list[dict[str, Any]],
    existing_totals: dict[str, float],
    targets: dict[str, Any],
    mode: Literal["ideal", "acceptable"],
    time_limit_seconds: float = 1.0,
) -> OptimizedMealDraft:
    return _solve(
        meals=meals,
        food_candidates=food_candidates,
        existing_totals=existing_totals,
        targets=targets,
        mode=mode,
        time_limit_seconds=time_limit_seconds,
    )


def validate_meal_usability(meals, reference, food_candidates) -> None:
    """Recheck the emitted portions and distribution, independent of MILP rows."""
    invalid = False
    if len(meals) != len(reference):
        invalid = True
    foods = {str(food["id"]): food for food in food_candidates}
    energies = []
    mains = []
    for index, (meal, original) in enumerate(zip(meals, reference)):
        if meal["meal_type"] != original["meal_type"] or len(meal["items"]) != len(original["items"]):
            invalid = True
        is_main = meal["meal_type"] in {"早餐", "午餐", "晚餐"}
        if is_main:
            mains.append(index)
        energy = 0.0
        substantial = False
        for item, source in zip(meal["items"], original["items"]):
            food = foods.get(str(item["food_id"]))
            if food is None or item["food_id"] != source["food_id"]:
                invalid = True
                continue
            amount = float(item["amount_g"])
            original_amount = float(source["amount_g"])
            oil = _is_oil(food)
            low, high, step = (1, 50, 1) if oil else (25, 500, 5)
            if not math.isfinite(amount):
                invalid = True
                continue
            invalid |= not (low <= amount <= high and original_amount * 0.5 - 1e-6 <= amount <= original_amount * 1.5 + 1e-6 and abs(amount / step - round(amount / step)) <= 1e-6)
            substantial |= not oil and amount >= 25
            energy += amount * float(food["calories_per_100g"]) / 100
        invalid |= is_main and not substantial
        energies.append(energy)
    total = sum(energies)
    invalid |= total <= 0
    bounds = {2: (0.25, 0.75), 3: (0.15, 0.5)}.get(len(mains))
    if total > 0:
        if bounds:
            invalid |= any(not bounds[0] - 1e-6 <= energies[i] / total <= bounds[1] + 1e-6 for i in mains)
        if mains:
            invalid |= sum(e for i, e in enumerate(energies) if i not in mains) / total > 0.15 + 1e-6
    if invalid:
        raise DailyMealOptimizationError("daily_meal_optimizer_unavailable", "营养配平结果未通过餐次与份量复验", violated_metrics=("meal_usability",))
