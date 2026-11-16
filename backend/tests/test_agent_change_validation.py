import pytest

from app.services.agent_change_validation import validate_semantic_changes
from app.services.agent_intent import ChangeRequest


def _validate(domain, kind, effect, changes):
    return validate_semantic_changes(
        intent_domain=domain,
        request_kind=kind,
        requested_effect=effect,
        change_requests=changes,
    )


def test_complete_meal_structure_is_authoritative():
    result = _validate(
        "nutrition",
        "mutation",
        "create",
        [ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "logged_at": "today",
                "meal_type": "晚餐",
                "items": [
                    {"food_name": "鸡胸肉", "amount_g": 150},
                    {"food_name": "杂粮饭", "amount_g": 100},
                ],
            },
        )],
    )

    assert result.complete is True
    assert result.missing_slots == ()


@pytest.mark.parametrize(
    ("value", "expected_slots", "question"),
    [
        (
            {
                "logged_at": "today",
                "meal_type": "晚餐",
                "items": [{"food_name": "鸡胸肉"}],
            },
            ("每种食品的克数",),
            "请补充鸡胸肉的克数。",
        ),
        (
            {
                "logged_at": "today",
                "items": [{"food_name": "鸡胸肉", "amount_g": 150}],
            },
            ("餐次",),
            "请明确这是早餐、午餐、晚餐还是加餐。",
        ),
        (
            {"logged_at": "today", "meal_type": "晚餐", "items": []},
            ("食品", "每种食品的克数"),
            "请补充食品、每种食品的克数，我再为你生成待确认提案。",
        ),
    ],
)
def test_meal_validation_derives_only_real_missing_slots(
    value, expected_slots, question
):
    result = _validate(
        "nutrition",
        "mutation",
        "create",
        [ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value=value,
        )],
    )

    assert result.missing_slots == expected_slots
    assert result.clarification_question == question


def test_profile_health_and_weight_complete_values_are_not_clarified():
    cases = [
        (
            "profile",
            "update",
            ChangeRequest(
                resource="profile",
                operation="update",
                field_path="profile.training_days_per_week",
                value=3,
            ),
        ),
        (
            "health",
            "update",
            ChangeRequest(
                resource="health",
                operation="update",
                field_path="health.injuries",
                value=[],
            ),
        ),
        (
            "profile",
            "create",
            ChangeRequest(
                resource="profile",
                operation="create",
                field_path="weight_log.weight_kg",
                value=65,
            ),
        ),
    ]

    for domain, effect, change in cases:
        assert _validate(domain, "mutation", effect, [change]).complete is True


def test_meal_date_is_validated_by_the_server():
    result = _validate(
        "nutrition",
        "mutation",
        "create",
        [ChangeRequest(
            resource="nutrition",
            operation="create",
            field_path="meal",
            value={
                "meal_type": "晚餐",
                "items": [{"food_name": "鸡胸肉", "amount_g": 150}],
            },
        )],
    )

    assert result.missing_slots == ("记录日期",)
    assert result.clarification_question == "请补充这条饮食记录的日期。"


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("schedule.duration_weeks", 13),
        ("schedule.days_per_week", 0),
        ("exercise.sets", 9),
        ("exercise.reps", ""),
        ("exercise.rest_seconds", 10),
        ("exercise.recommended_weight_kg", 1001),
    ],
)
def test_plan_out_of_range_values_are_rejected_before_compilation(
    field_path, value
):
    result = _validate(
        "workout_plan",
        "mutation",
        "update",
        [ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path=field_path,
            target_reference=(
                "深蹲" if field_path.startswith("exercise.") else None
            ),
            value=value,
        )],
    )

    assert result.missing_slots == ("计划调整的有效目标值",)


@pytest.mark.parametrize(("raw_value", "normalized"), [(8, "8"), (8.0, "8")])
def test_integral_numeric_reps_are_canonicalized_before_validation(
    raw_value, normalized
):
    change = ChangeRequest(
        resource="workout_plan",
        operation="update",
        field_path="exercise.reps",
        target_reference="卧推",
        value=raw_value,
    )

    result = _validate(
        "workout_plan",
        "mutation",
        "update",
        [change],
    )

    assert change.value == normalized
    assert result.complete is True


@pytest.mark.parametrize("raw_value", [0, -8, 8.5, True])
def test_non_positive_or_non_integral_numeric_reps_remain_invalid(raw_value):
    change = ChangeRequest(
        resource="workout_plan",
        operation="update",
        field_path="exercise.reps",
        target_reference="卧推",
        value=raw_value,
    )

    result = _validate(
        "workout_plan",
        "mutation",
        "update",
        [change],
    )

    assert result.missing_slots == ("计划调整的有效目标值",)


def test_plan_target_and_value_requirements_are_typed():
    missing_target = _validate(
        "workout_plan",
        "mutation",
        "update",
        [ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="exercise.sets",
            value=3,
        )],
    )
    missing_value = _validate(
        "workout_plan",
        "mutation",
        "update",
        [ChangeRequest(
            resource="workout_plan",
            operation="update",
            field_path="schedule.duration_weeks",
            value=None,
        )],
    )

    assert missing_target.missing_slots == ("要调整的动作名称",)
    assert missing_value.missing_slots == ("计划调整的具体目标值",)


def test_proposal_decision_requires_one_valid_action():
    valid = _validate(
        "general",
        "proposal_decision",
        "decide",
        [ChangeRequest(
            resource="general",
            operation="update",
            field_path="proposal.status",
            value="confirm",
        )],
    )
    invalid = _validate(
        "general", "proposal_decision", "decide", []
    )

    assert valid.complete is True
    assert invalid.missing_slots == ("确认或拒绝动作",)


def test_conflicting_duplicate_change_targets_require_clarification():
    result = _validate(
        "profile",
        "mutation",
        "update",
        [
            ChangeRequest(
                resource="profile",
                operation="update",
                field_path="profile.training_days_per_week",
                value=3,
            ),
            ChangeRequest(
                resource="profile",
                operation="update",
                field_path="profile.training_days_per_week",
                value=4,
            ),
        ],
    )

    assert result.missing_slots == ("唯一的变更目标和值",)
