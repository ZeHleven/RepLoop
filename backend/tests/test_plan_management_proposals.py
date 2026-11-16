from app.schemas.plan_management_proposal import (
    PlanExerciseSnapshotV2,
    PlanSnapshotV2,
)
from app.services.plan_management_proposals import (
    compile_plan_changes,
    plan_snapshot_fingerprint,
)


def _exercise(
    key: str,
    exercise_id: str,
    name: str,
    *,
    day: int = 1,
    order: int = 0,
    sets: int = 3,
) -> PlanExerciseSnapshotV2:
    return PlanExerciseSnapshotV2(
        item_key=key,
        exercise_id=exercise_id,
        exercise_name=name,
        category="力量",
        day_of_week=day,
        sets=sets,
        reps="8-12",
        rest_seconds=90,
        recommended_weight_kg=None,
        order_index=order,
    )


def _plan(exercises, *, duration=4, days=(1,)) -> PlanSnapshotV2:
    return PlanSnapshotV2(
        name="完整计划",
        goal="general_fitness",
        duration_weeks=duration,
        days_per_week=len(days),
        training_days=list(days),
        exercises=exercises,
    )


def test_plan_v2_diff_compiles_every_supported_change_type():
    before = _plan([
        _exercise("planned:a", "squat", "深蹲"),
        _exercise("planned:b", "row", "划船", order=1),
        _exercise("planned:removed", "curl", "弯举", order=2),
    ])
    after = _plan([
        _exercise("planned:a", "goblet", "高脚杯深蹲", day=3, sets=4),
        _exercise("planned:b", "row", "划船", order=0),
        _exercise("new:new-action", "pushup", "俯卧撑", day=3, order=1),
    ], duration=6, days=(1, 3))

    types = {item.change_type for item in compile_plan_changes(before, after)}

    assert types == {
        "update_schedule",
        "add_exercise",
        "remove_exercise",
        "replace_exercise",
        "move_exercise",
        "adjust_exercise_target",
    }


def test_plan_v2_fingerprint_uses_semantics_not_database_item_keys():
    first = _plan([_exercise("planned:old-row", "squat", "深蹲")])
    second = _plan([_exercise("planned:new-row", "squat", "深蹲")])

    assert plan_snapshot_fingerprint(first) == plan_snapshot_fingerprint(second)


def test_plan_v2_no_change_produces_no_operations():
    snapshot = _plan([_exercise("planned:a", "squat", "深蹲")])

    assert compile_plan_changes(snapshot, snapshot.model_copy(deep=True)) == []
