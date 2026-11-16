import pytest

from app.models.exercise import Exercise
from app.models.profile import UserProfile
from app.schemas.workout import PersonalizedPlanPreviewRequest
from app.services.personalized_planner import (
    PersonalizedPlanError,
    build_personalized_plan_preview,
)


def _profile(**overrides) -> UserProfile:
    values = {
        "id": "profile-1",
        "user_id": "user-1",
        "onboarding_completed": True,
        "primary_goal": "general_fitness",
        "experience_level": "beginner",
        "training_days_per_week": 3,
        "session_duration_min": 45,
        "training_location": "gym",
        "injuries": [],
        "chronic_conditions": [],
    }
    values.update(overrides)
    return UserProfile(**values)


def _exercise(
    exercise_id: str,
    name: str,
    *,
    pattern: str,
    equipment: list[str],
    difficulty: str = "初级",
) -> Exercise:
    return Exercise(
        id=exercise_id,
        name_zh=name,
        name_en=exercise_id,
        category="力量",
        muscle_primary=["core"],
        movement_pattern=pattern,
        equipment=equipment,
        difficulty=difficulty,
        rep_range_min=8,
        rep_range_max=15,
        sets_range_min=2,
        sets_range_max=4,
        is_active=True,
    )


EXERCISES = [
    _exercise("squat", "高脚杯深蹲", pattern="squat", equipment=["dumbbell"]),
    _exercise("pushup", "俯卧撑", pattern="push", equipment=["bodyweight"]),
    _exercise("row", "坐姿划船", pattern="pull", equipment=["cable"]),
    _exercise("hinge", "罗马尼亚硬拉", pattern="hinge", equipment=["barbell"], difficulty="中级"),
    _exercise("plank", "平板支撑", pattern="isometric", equipment=["bodyweight"]),
    _exercise("crunch", "卷腹", pattern="flex", equipment=["bodyweight"]),
]


def test_preview_uses_profile_schedule_and_session_duration():
    preview = build_personalized_plan_preview(_profile(), EXERCISES)

    assert preview.days_per_week == 3
    assert {item.day_of_week for item in preview.exercises} == {1, 3, 5}
    assert len(preview.exercises) == 12
    assert all(item.sets == 2 for item in preview.exercises)
    assert preview.generation_strategy == "profile_rules_v1"


@pytest.mark.parametrize("days", [[2, 4, 7], [7], [6, 7], [1, 2, 3, 4, 5, 6, 7]])
def test_explicit_weekdays_override_profile_and_derive_frequency(days):
    request = PersonalizedPlanPreviewRequest(training_days=list(reversed(days)))
    preview = build_personalized_plan_preview(_profile(), EXERCISES, request)
    assert preview.days_per_week == len(days)
    assert sorted({item.day_of_week for item in preview.exercises}) == days
    assert request.training_days == days
    assert any("周日" in value for value in preview.rationale)


@pytest.mark.parametrize("days", [[], [1, 1], [0], [8], [True], [1.5], ["2"]])
def test_invalid_explicit_weekdays_are_rejected(days):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PersonalizedPlanPreviewRequest(training_days=days)


def test_explicit_weekday_count_must_match_frequency():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PersonalizedPlanPreviewRequest(training_days=[2, 7], days_per_week=3)


def test_explicit_weekdays_preserve_health_filtering_and_targets():
    profile = _profile(injuries=["膝关节"], chronic_conditions=["高血压"])
    legacy = build_personalized_plan_preview(profile, EXERCISES)
    custom = build_personalized_plan_preview(profile, EXERCISES,
        PersonalizedPlanPreviewRequest(training_days=[2, 4, 7]))
    assert [item.model_dump(exclude={"day_of_week"}) for item in custom.exercises] == [
        item.model_dump(exclude={"day_of_week"}) for item in legacy.exercises]
    assert all(item.exercise_id != "squat" and item.rest_seconds == 120 for item in custom.exercises)


def test_preview_home_location_only_offers_bodyweight_actions():
    preview = build_personalized_plan_preview(
        _profile(training_location="home", training_days_per_week=2),
        EXERCISES,
    )

    assert {option.exercise_id for option in preview.exercise_options} == {"pushup", "crunch"}
    assert {item.exercise_id for item in preview.exercises} == {"pushup", "crunch"}


def test_preview_filters_injury_risk_and_adds_safety_explanation():
    preview = build_personalized_plan_preview(
        _profile(injuries=["膝关节"]),
        EXERCISES,
    )

    assert "squat" not in {option.exercise_id for option in preview.exercise_options}
    assert any("膝关节" in item for item in preview.rationale)
    assert any("膝关节" in item for item in preview.safety_notes)


def test_preview_chronic_condition_uses_conservative_volume():
    preview = build_personalized_plan_preview(
        _profile(
            experience_level="advanced",
            chronic_conditions=["高血压"],
        ),
        EXERCISES,
        PersonalizedPlanPreviewRequest(days_per_week=2, session_duration_min=60),
    )

    assert all(item.sets == 2 for item in preview.exercises)
    assert all(item.rest_seconds == 120 for item in preview.exercises)
    assert any("高血压" in item for item in preview.safety_notes)


def test_preview_requires_completed_onboarding():
    with pytest.raises(PersonalizedPlanError, match="健康筛查"):
        build_personalized_plan_preview(
            _profile(onboarding_completed=False),
            EXERCISES,
        )


@pytest.mark.parametrize("location", ["gym", "home", "outdoor"])
def test_seed_library_can_generate_for_each_supported_location(location):
    from scripts.seed_exercises import EXERCISES as SEED_EXERCISES

    library = [
        Exercise(id=f"seed-{index}", is_active=True, **data)
        for index, data in enumerate(SEED_EXERCISES)
    ]
    preview = build_personalized_plan_preview(
        _profile(training_location=location),
        library,
    )

    assert preview.exercises
    assert {item.day_of_week for item in preview.exercises} == {1, 3, 5}
