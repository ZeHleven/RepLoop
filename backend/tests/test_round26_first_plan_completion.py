"""First plan changes may fulfill sourced gaps without dropping unknown goals."""
import pytest

from app.schemas.agent_task import ConversationTask, PlanInputGap, TaskSnapshot, TaskUpdate
from app.services.agent_intent import ChangeRequest
from app.services.agent_task_state import preserve_pending_plan_changes


def pending_context(quote="卧推次数稍后再补", extra_requirements=()):
    task = ConversationTask(
        id="plan", request="调整当前训练计划的卧推次数，等用户补充次数",
        phase="awaiting_input", last_run_id="first",
        requirements=[{"key": "bench_reps", "quote": quote, "source_run_id": "first"},
                      *extra_requirements],
        plan_input_gaps=[PlanInputGap(field_path="exercise.reps", target_reference="卧推", quote=quote)],
    )
    return [{"role": "task_state", "content": TaskSnapshot(active_task_id="plan", tasks=[task]).model_dump_json()}]


def complete(context, message="卧推次数设为8次，现在生成完整提案。", *,
             target="卧推", field="reps", value=8, key="bench_reps", quote=None, remove=False):
    update = TaskUpdate(action="continue", task_id="plan", trigger=message,
                        requirements=[{"key": key, "quote": quote or message, "remove": remove}])
    changes = [ChangeRequest(resource="workout_plan", operation="update", field_path="exercise." + field,
                             target_reference=target, value=value)]
    return preserve_pending_plan_changes(context, update, changes, message=message)


@pytest.mark.parametrize("remove", [False, True])
def test_first_sourced_change_fulfills_pending_requirement(remove):
    result = complete(pending_context(), remove=remove)
    assert [(c.target_reference, c.field_path, c.value) for c in result] == [("卧推", "exercise.reps", "8")]


@pytest.mark.parametrize("overrides", [
    {"target": "深蹲"}, {"field": "sets"}, {"value": 9}, {"key": "unrelated"},
    {"quote": "卧推次数设为9次"},
    {"message": "卧推次数不要设为8次，现在生成完整提案。"},
    {"message": "现在生成完整提案。"},
])
def test_first_completion_requires_same_requirement_target_field_and_source(overrides):
    with pytest.raises(ValueError, match="task_plan_unresolved_requirements"):
        complete(pending_context(), **overrides)


def test_unstructured_old_goal_cannot_disappear_when_gap_is_filled():
    context = pending_context(extra_requirements=[{"key": "squat_sets", "quote": "深蹲4组", "source_run_id": "first"}])
    with pytest.raises(ValueError, match="task_plan_unresolved_requirements"):
        complete(context)


@pytest.mark.parametrize("quote", ["卧推次数稍后再补，深蹲4组", "卧推次数稍后再补，深蹲四组"])
def test_mixed_gap_quote_does_not_hide_an_unstructured_scalar_goal(quote):
    with pytest.raises(ValueError, match="task_plan_unresolved_requirements"):
        complete(pending_context(quote))


def test_requirement_must_not_extend_beyond_its_saved_gap():
    context = pending_context()
    state = TaskSnapshot.model_validate_json(context[0]["content"])
    state.tasks[0].requirements[0].quote += "，另调整深蹲动作"
    context[0]["content"] = state.model_dump_json()
    with pytest.raises(ValueError, match="task_plan_unresolved_requirements"):
        complete(context)


def test_explicitly_repeated_old_goal_keeps_existing_behavior():
    context = pending_context(extra_requirements=[{"key": "squat_sets", "quote": "深蹲4组", "source_run_id": "first"}])
    result = complete(context, message="卧推次数设为8次，深蹲4组，现在生成完整提案。")
    assert result[0].value == "8"


@pytest.mark.parametrize("quote", ["先不要生成提案，去掉深蹲动作", "先不要生成提案，训练地点改为家里"])
def test_workflow_quote_cannot_hide_unknown_non_scalar_goal(quote):
    with pytest.raises(ValueError, match="task_plan_unresolved_requirements"):
        complete(pending_context(quote), quote="现在生成完整提案", remove=True)


@pytest.mark.parametrize("quote", [
    "卧推次数稍后再补，去掉深蹲动作",
    "卧推次数稍后再补，训练地点改为家里",
    "卧推次数稍后再补并去掉深蹲动作",
])
def test_exact_gap_quote_must_be_only_a_deferred_parameter(quote):
    with pytest.raises(ValueError, match="task_plan_unresolved_requirements"):
        complete(pending_context(quote), remove=True)
