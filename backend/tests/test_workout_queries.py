from app.services.workout_queries import (
    best_performance,
    is_personal_record,
    normalized_sets,
    sets_metrics,
)


def test_sets_metrics_ignores_invalid_rows_and_calculates_volume():
    assert sets_metrics([
        {"reps": 10, "weight_kg": 20},
        {"reps": 8, "weight_kg": 22.5},
        {"reps": 0, "weight_kg": 100},
        "invalid",
    ]) == (2, 18, 380.0)


def test_normalized_sets_returns_copies_of_mapping_rows():
    original = {"set_number": 1, "reps": 8}
    result = normalized_sets([original, None])

    assert result == [original]
    assert result[0] is not original


def test_best_performance_prefers_weight_then_repetitions():
    assert best_performance([
        {"reps": 12, "weight_kg": 20},
        {"reps": 6, "weight_kg": 25},
        {"reps": 8, "weight_kg": 25},
    ]) == (25.0, 8)
    assert best_performance([{"reps": 15, "weight_kg": None}]) == (None, 15)


def test_personal_record_uses_the_same_ordering_as_history_display():
    baseline = [
        {"reps": 10, "weight_kg": 20},
        {"reps": 6, "weight_kg": 25},
    ]

    assert is_personal_record({"reps": 7, "weight_kg": 25}, baseline) is True
    assert is_personal_record({"reps": 20, "weight_kg": 24}, baseline) is False
