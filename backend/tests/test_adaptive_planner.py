import pytest
from pydantic import ValidationError

from app.models.exercise import Exercise
from app.models.workout import PlannedExercise, SessionExercise, WorkoutSession
from app.schemas.workout import WorkoutFeedback
from app.services.adaptive_planner import (
    AdaptiveAdjustmentProposal,
    build_adaptive_adjustment_proposals,
    decide_exercise_adjustment,
    parse_rep_range,
    shift_rep_range,
)


def test_completed_weighted_target_progresses_load_conservatively():
    decision = decide_exercise_adjustment(
        target_sets=3,
        target_reps="8-10",
        rest_seconds=90,
        sets_data=[
            {"reps": 10, "weight_kg": 40},
            {"reps": 10, "weight_kg": 40},
            {"reps": 10, "weight_kg": 40},
        ],
        feedback=WorkoutFeedback(
            difficulty_feedback="just_right",
            perceived_exertion=7,
            energy_level=3,
        ),
    )

    assert decision.action == "increase_weight"
    assert decision.recommended_weight_kg == 41
    assert decision.sets == 3


def test_hard_incomplete_weighted_target_reduces_weight_and_sets():
    decision = decide_exercise_adjustment(
        target_sets=3,
        target_reps="8-10",
        rest_seconds=90,
        sets_data=[{"reps": 6, "weight_kg": 50}],
        feedback=WorkoutFeedback(
            difficulty_feedback="too_hard",
            perceived_exertion=10,
            energy_level=1,
        ),
    )

    assert decision.action == "decrease_weight_and_sets"
    assert decision.recommended_weight_kg == 45
    assert decision.sets == 2
    assert decision.rest_seconds == 120


def test_easy_bodyweight_target_increases_reps():
    decision = decide_exercise_adjustment(
        target_sets=2,
        target_reps="10-12",
        rest_seconds=60,
        sets_data=[{"reps": 12, "weight_kg": None}, {"reps": 13, "weight_kg": None}],
        feedback=WorkoutFeedback(difficulty_feedback="too_easy", perceived_exertion=5),
    )

    assert decision.action == "increase_reps"
    assert decision.reps == "12-14"
    assert decision.recommended_weight_kg is None


def test_pain_feedback_takes_priority_over_progression():
    decision = decide_exercise_adjustment(
        target_sets=3,
        target_reps="8-10",
        rest_seconds=90,
        sets_data=[
            {"reps": 10, "weight_kg": 40},
            {"reps": 10, "weight_kg": 40},
            {"reps": 10, "weight_kg": 40},
        ],
        feedback=WorkoutFeedback(
            difficulty_feedback="too_easy",
            perceived_exertion=5,
            pain_level=5,
            pain_areas=["膝关节"],
        ),
    )

    assert decision.action == "reduce_for_pain"
    assert decision.safety_priority is True
    assert decision.sets == 2
    assert decision.recommended_weight_kg == 36


def test_mild_pain_and_low_completion_uses_cautious_reduction():
    decision = decide_exercise_adjustment(
        target_sets=3,
        target_reps="8-10",
        rest_seconds=90,
        sets_data=[{"reps": 7, "weight_kg": 40}],
        feedback=WorkoutFeedback(
            difficulty_feedback="just_right",
            perceived_exertion=7,
            pain_level=2,
            pain_areas=["肩关节"],
        ),
    )

    assert decision.action == "reduce_for_caution"
    assert decision.safety_priority is True
    assert decision.sets == 2
    assert decision.recommended_weight_kg == 38


def test_appropriate_effort_maintains_target():
    decision = decide_exercise_adjustment(
        target_sets=3,
        target_reps="8-10",
        rest_seconds=90,
        sets_data=[
            {"reps": 8, "weight_kg": 40},
            {"reps": 9, "weight_kg": 40},
            {"reps": 8, "weight_kg": 40},
        ],
        feedback=WorkoutFeedback(
            difficulty_feedback="just_right",
            perceived_exertion=8,
            energy_level=3,
        ),
    )

    assert decision.action == "maintain"
    assert decision.recommended_weight_kg == 40
    assert decision.reps == "8-10"


def test_rep_range_helpers_are_bounded_and_stable():
    assert parse_rep_range("12") == (12, 12)
    assert parse_rep_range("8-10") == (8, 10)
    assert shift_rep_range("1", -2) == "1"


def test_pain_feedback_requires_a_body_area():
    with pytest.raises(ValidationError, match="疼痛部位"):
        WorkoutFeedback(pain_level=4)


def test_adjustment_proposal_hides_internal_target_id_from_api_response():
    proposal = AdaptiveAdjustmentProposal(
        planned_exercise_id="planned-1",
        exercise_id="exercise-1",
        exercise_name="深蹲",
        action="maintain",
        before={"sets": 3},
        after={"sets": 3},
        reason="维持当前目标",
    )

    response = proposal.to_response()

    assert "planned_exercise_id" not in response
    assert response["exercise_id"] == "exercise-1"
    assert response["after"] == {"sets": 3}


class _FakeScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _ProposalOnlyDb:
    def __init__(self, planned, exercises):
        self._results = iter((planned, exercises))

    async def execute(self, _query):
        return _FakeScalarResult(next(self._results))

    async def scalar(self, _query):
        return None


@pytest.mark.asyncio
async def test_build_adjustment_proposal_does_not_mutate_plan_entity():
    planned = PlannedExercise(
        id="planned-1",
        plan_id="plan-1",
        exercise_id="exercise-1",
        day_of_week=1,
        sets=3,
        reps="8-10",
        rest_seconds=90,
        recommended_weight_kg=40,
        order_index=0,
    )
    exercise = Exercise(
        id="exercise-1",
        name_zh="深蹲",
        name_en="Squat",
        category="力量",
        muscle_primary=["腿"],
        difficulty="初级",
        is_active=True,
    )
    session = WorkoutSession(
        id="session-1",
        user_id="user-1",
        plan_id="plan-1",
        day_of_week=1,
    )
    session_exercise = SessionExercise(
        id="session-exercise-1",
        session_id="session-1",
        exercise_id="exercise-1",
        order_index=0,
        target_sets=3,
        target_reps="8-10",
        rest_seconds=90,
        sets_data=[
            {"reps": 10, "weight_kg": 40},
            {"reps": 10, "weight_kg": 40},
            {"reps": 10, "weight_kg": 40},
        ],
    )
    db = _ProposalOnlyDb([planned], [exercise])

    proposals = await build_adaptive_adjustment_proposals(
        db,
        session=session,
        session_exercises=[session_exercise],
        feedback=WorkoutFeedback(
            difficulty_feedback="just_right",
            perceived_exertion=7,
        ),
    )

    assert len(proposals) == 1
    assert proposals[0].after["recommended_weight_kg"] == 41
    assert planned.recommended_weight_kg == 40
    assert planned.sets == 3
