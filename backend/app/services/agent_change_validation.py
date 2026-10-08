from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any, Iterable, Protocol
from app.services.business_clock import business_today


class SemanticChange(Protocol):
    resource: str
    operation: str
    field_path: str | None
    target_reference: str | None
    value: Any


@dataclass(frozen=True)
class SemanticChangeValidation:
    missing_slots: tuple[str, ...] = ()
    clarification_question: str | None = None

    @property
    def complete(self) -> bool:
        return not self.missing_slots


_PLAN_VALUE_FIELDS = frozenset({
    "schedule.duration_weeks",
    "schedule.days_per_week",
    "exercise.sets",
    "exercise.reps",
    "exercise.rest_seconds",
    "exercise.recommended_weight_kg",
    "exercise.exercise_id",
    "exercise.day_of_week",
})
_PLAN_TARGET_FIELDS = frozenset({
    "exercise.sets",
    "exercise.reps",
    "exercise.rest_seconds",
    "exercise.recommended_weight_kg",
    "exercise.exercise_id",
    "exercise.day_of_week",
})
_PROFILE_FIELDS = frozenset({
    "profile.age",
    "profile.gender",
    "profile.height_cm",
    "profile.weight_kg",
    "profile.experience_level",
    "profile.primary_goal",
    "profile.training_days_per_week",
    "profile.session_duration_min",
    "profile.training_location",
    "profile.diet_restriction",
    "health.injuries",
    "health.chronic_conditions",
})
_WEIGHT_FIELDS = frozenset({
    "weight_log.weight_kg",
    "profile.weight_kg",
    "weight_kg",
})
_MEAL_TYPES = frozenset({"早餐", "午餐", "晚餐", "加餐"})


def _is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _is_int_in_range(value: Any, minimum: int, maximum: int) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and minimum <= value <= maximum
    )


def _is_number_in_range(value: Any, minimum: float, maximum: float) -> bool:
    return _is_number(value) and minimum <= float(value) <= maximum


def _valid_plan_creation_options(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value) - {
        "goal", "duration_weeks", "days_per_week", "session_duration_min"
    }:
        return False
    return (
        (
            "goal" not in value
            or value["goal"] is None
            or isinstance(value["goal"], str)
            and 0 < len(value["goal"].strip()) <= 50
        )
        and (
            "duration_weeks" not in value
            or _is_int_in_range(value["duration_weeks"], 2, 12)
        )
        and (
            "days_per_week" not in value
            or _is_int_in_range(value["days_per_week"], 1, 7)
        )
        and (
            "session_duration_min" not in value
            or _is_int_in_range(value["session_duration_min"], 20, 120)
        )
    )


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _has_duplicate_targets(changes: list[SemanticChange]) -> bool:
    keys = [
        (
            change.resource,
            change.operation,
            change.field_path,
            (change.target_reference or "").strip().casefold(),
        )
        for change in changes
    ]
    return len(keys) != len(set(keys))


def _question_for(missing_slots: tuple[str, ...]) -> str | None:
    if not missing_slots:
        return None
    slots = set(missing_slots)
    if slots == {"餐次"}:
        return "请明确这是早餐、午餐、晚餐还是加餐。"
    if slots == {"记录日期"}:
        return "请补充这条饮食记录的日期。"
    if slots == {"食品"}:
        return "请告诉我这条饮食记录包含哪些食品。"
    if slots == {"每种食品的克数"}:
        return "请补充每种食品的克数。"
    if slots <= {"餐次", "食品", "每种食品的克数"}:
        return f"请补充{'、'.join(missing_slots)}，我再为你生成待确认提案。"
    if slots == {"确认或拒绝动作"}:
        return "请明确告诉我是确认还是拒绝当前待确认提案。"
    return f"请补充{'、'.join(missing_slots)}，我再继续处理。"


def _missing_plan_slots(changes: list[SemanticChange], effect: str) -> list[str]:
    missing: list[str] = []
    if not changes:
        return [
            "要修改的计划项目和具体目标值"
            if effect == "update"
            else "要创建或删除的训练计划",
        ]
    for change in changes:
        if change.resource != "workout_plan":
            missing.append("训练计划写入对象")
            continue
        field = change.field_path
        if (
            change.operation == "delete"
            and field in {None, "plan", "workout_plan"}
        ):
            continue
        if effect == "create":
            if change.operation != "create":
                missing.append("新计划的创建操作")
            elif field == "schedule.days_per_week":
                if not _is_int_in_range(change.value, 1, 7):
                    missing.append("新计划的有效每周训练天数")
            elif field == "schedule.duration_weeks":
                if not _is_int_in_range(change.value, 2, 12):
                    missing.append("新计划的有效周期")
            elif field == "plan.goal":
                if not (
                    isinstance(change.value, str)
                    and 0 < len(change.value.strip()) <= 50
                ):
                    missing.append("新计划的有效目标")
            elif field == "plan":
                if not _valid_plan_creation_options(change.value):
                    missing.append("新计划的目标或安排")
            else:
                missing.append("新计划的受支持选项")
            continue
        if change.operation == "delete" and field in {None, "exercise", "exercise.delete"}:
            if not change.target_reference:
                missing.append("要删除的动作名称")
            continue
        if change.operation == "create" and field in {"exercise", "exercise.add"}:
            value = change.value if isinstance(change.value, dict) else {}
            if not (value.get("exercise_id") or value.get("exercise_name")):
                missing.append("要新增的动作名称")
            if value.get("day_of_week") is None:
                missing.append("新增动作的训练日")
            continue
        if change.operation != "update" or field not in _PLAN_VALUE_FIELDS:
            missing.append("受支持的训练计划变更字段")
            continue
        value = change.value
        valid_value = {
            "schedule.duration_weeks": _is_int_in_range(value, 2, 12),
            "schedule.days_per_week": _is_int_in_range(value, 1, 7),
            "exercise.sets": _is_int_in_range(value, 1, 8),
            "exercise.reps": (
                isinstance(value, str)
                and bool(value.strip())
                and len(value.strip()) <= 20
            ),
            "exercise.rest_seconds": _is_int_in_range(value, 15, 600),
            "exercise.recommended_weight_kg": _is_number_in_range(
                value, 0, 1000
            ),
            "exercise.exercise_id": (
                isinstance(value, (str, dict)) and bool(value)
            ),
            "exercise.day_of_week": _is_int_in_range(value, 1, 7),
        }[field]
        if not valid_value:
            missing.append(
                "计划调整的具体目标值"
                if value is None
                else "计划调整的有效目标值"
            )
        if field in _PLAN_TARGET_FIELDS and not change.target_reference:
            missing.append("要调整的动作名称")
    return missing


def _missing_profile_slots(
    changes: list[SemanticChange],
    *,
    domain: str,
    effect: str,
) -> list[str]:
    if not changes:
        return [
            "体重数值"
            if effect == "create"
            else "要更新的资料字段和具体值",
        ]
    missing: list[str] = []
    for change in changes:
        if change.resource not in {"profile", "health"}:
            missing.append("档案或健康写入对象")
            continue
        if effect == "create":
            if (
                change.operation != "create"
                or change.field_path not in _WEIGHT_FIELDS
                or not _is_number_in_range(change.value, 25, 350)
            ):
                missing.append("体重数值")
            continue
        field = change.field_path
        if (
            change.operation != "update"
            or not field
            or field not in _PROFILE_FIELDS
        ):
            missing.append("要更新的资料字段")
            continue
        value = change.value
        if field == "profile.age":
            valid_value = _is_int_in_range(value, 12, 100)
        elif field == "profile.gender":
            valid_value = isinstance(value, str) and value in {
                "male", "female", "prefer_not_to_say"
            }
        elif field == "profile.height_cm":
            valid_value = _is_number_in_range(value, 100, 250)
        elif field == "profile.weight_kg":
            valid_value = _is_number_in_range(value, 25, 350)
        elif field == "profile.training_days_per_week":
            valid_value = _is_int_in_range(value, 1, 7)
        elif field == "profile.session_duration_min":
            valid_value = _is_int_in_range(value, 10, 300)
        elif field in {
            "profile.experience_level",
            "profile.primary_goal",
            "profile.training_location",
        }:
            valid_value = isinstance(value, str) and bool(value.strip())
        elif field == "profile.diet_restriction":
            valid_value = isinstance(value, str)
        else:
            valid_value = (
                isinstance(value, list)
                and len(value) <= 12
                and all(
                    isinstance(item, str)
                    and bool(item.strip())
                    and len(item.strip()) <= 50
                    for item in value
                )
            )
        if not valid_value:
            missing.append("资料字段的具体值")
        if domain == "health" and field in {
            "health.injuries", "health.chronic_conditions"
        } and not isinstance(value, list):
            missing.append("完整的健康资料列表")
    return missing


@dataclass(frozen=True)
class MealItemGap:
    index: int
    label: str
    field: str


def _meal_item_gaps(items: list) -> tuple[MealItemGap, ...]:
    def name_of(item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        name = item.get("food_name") or item.get("food_reference")
        return name.strip() if isinstance(name, str) else ""

    names = [name_of(item) for item in items]
    gaps = []
    for index, item in enumerate(items, 1):
        value = item if isinstance(item, dict) else {}
        name = names[index - 1]
        label = name or f"第{index}项"
        if name and names.count(name) > 1:
            label = f"第{index}项（{name}）"
        if not (str(value.get("food_id") or "").strip() or name):
            gaps.append(MealItemGap(index, label, "food"))
        if not _is_number_in_range(value.get("amount_g"), 0.000001, 10000):
            gaps.append(MealItemGap(index, label, "amount"))
    return tuple(gaps)


def _meal_question(changes: list[SemanticChange], missing: tuple[str, ...]) -> str | None:
    # Keep the existing slot contract for pending-state consumers, but preserve
    # item identity when rendering a question. Never display a database ID.
    if len(changes) != 1 or not set(missing) & {"食品", "每种食品的克数"}:
        return _question_for(missing)
    value = changes[0].value
    items = value.get("items") if isinstance(value, dict) else None
    if not isinstance(items, list) or not items or len(items) > 30:
        return _question_for(missing)
    labels = [slot for slot in missing if slot not in {"食品", "每种食品的克数"}]
    labels.extend(
        f"{gap.label}的{'食品名称' if gap.field == 'food' else '克数'}"
        for gap in _meal_item_gaps(items)
    )
    return "请补充" + "、".join(labels) + "。"


def _missing_meal_slots(changes: list[SemanticChange], effect: str) -> list[str]:
    if (
        effect == "create"
        and len(changes) == 1
        and changes[0].resource == "nutrition"
        and changes[0].operation == "create"
        and changes[0].field_path == "daily_meal_plan.save"
        and changes[0].target_reference
    ):
        return []
    if effect == "delete":
        if len(changes) != 1:
            return ["要删除的具体餐次记录"]
        change = changes[0]
        if change.resource != "nutrition" or change.operation != "delete":
            return ["要删除的具体餐次记录"]
        if not change.target_reference and change.value is None:
            return ["要删除的具体餐次记录"]
        return []
    if not changes:
        return ["餐次", "食品", "每种食品的克数"]
    if len(changes) != 1:
        return ["一条完整餐次"]
    change = changes[0]
    if (
        change.resource != "nutrition"
        or change.operation != "create"
        or change.field_path not in {"meal", "meal_log", "meal.items"}
    ):
        return ["一条完整餐次"]
    value = change.value if isinstance(change.value, dict) else {}
    missing: list[str] = []
    logged_at = value.get("logged_at")
    valid_date = False
    if isinstance(logged_at, str) and logged_at in {"today", "今天"}:
        valid_date = True
    elif logged_at is not None:
        try:
            valid_date = date.fromisoformat(str(logged_at)) <= business_today()
        except ValueError:
            valid_date = False
    if not valid_date:
        missing.append("记录日期")
    if str(value.get("meal_type") or "").strip() not in _MEAL_TYPES:
        missing.append("餐次")
    items = value.get("items")
    if not isinstance(items, list) or not items or len(items) > 30:
        missing.extend(["食品", "每种食品的克数"])
        return missing
    gaps = _meal_item_gaps(items)
    if any(gap.field == "food" for gap in gaps):
        missing.append("食品")
    if any(gap.field == "amount" for gap in gaps):
        missing.append("每种食品的克数")
    return missing


def validate_semantic_changes(
    *,
    intent_domain: str,
    request_kind: str,
    requested_effect: str,
    change_requests: Iterable[SemanticChange],
) -> SemanticChangeValidation:
    changes = list(change_requests)
    if request_kind == "proposal_decision":
        valid = (
            len(changes) == 1
            and changes[0].field_path == "proposal.status"
            and changes[0].value in {"confirm", "reject"}
        )
        missing = () if valid else ("确认或拒绝动作",)
        return SemanticChangeValidation(missing, _question_for(missing))
    if request_kind != "mutation":
        return SemanticChangeValidation()

    if intent_domain == "workout_plan":
        missing_values = _missing_plan_slots(changes, requested_effect)
    elif intent_domain in {"profile", "health"}:
        missing_values = _missing_profile_slots(
            changes,
            domain=intent_domain,
            effect=requested_effect,
        )
    elif intent_domain == "nutrition":
        missing_values = _missing_meal_slots(changes, requested_effect)
    elif not changes:
        missing_values = ["写入对象和具体目标值"]
    else:
        missing_values = []
    if _has_duplicate_targets(changes):
        missing_values.append("唯一的变更目标和值")
    missing = _dedupe(missing_values)
    question = (
        _meal_question(changes, missing)
        if intent_domain == "nutrition" and requested_effect == "create"
        else _question_for(missing)
    )
    return SemanticChangeValidation(missing, question)
