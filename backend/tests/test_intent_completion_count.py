"""SCOPE-20261029: completion-count questions are reads; unspecified plan
reductions get a concrete clarification (found by dev set v2 Q04/Q06)."""
import pytest

from app.services.agent_intent import resolve_intent


@pytest.mark.parametrize("message", [
    "帮我查一下这个月一共完成了几次训练",
    "上周膝盖疼请假了两次，帮我查一下这个月一共完成了几次训练",
    "这周完成了多少次？",
    "十月一共完成几场训练呢",
])
def test_completion_count_questions_are_reads(message):
    resolution = resolve_intent(message)
    assert resolution.request_kind != "mutation" and resolution.requested_effect == "read"


@pytest.mark.parametrize("message", [
    "帮我完成训练并保存",
    "今天的训练帮我完成",
    "把这次训练标记为已完成",
])
def test_real_completion_commands_stay_writes(message):
    assert resolve_intent(message).request_kind == "mutation"


def test_unspecified_plan_change_asks_for_concrete_options():
    from app.schemas.agent_task import PlanInputGap, TaskSnapshot
    from app.services.agent_intent import ChangeRequest, IntentResolution
    from app.services.agent_plan_completeness import enforce_plan_completeness

    message = "帮我把这周训练减一点"
    resolution = IntentResolution(
        primary_intent="plan_query", intent_domain="workout_plan", request_kind="mutation",
        requested_effect="update", confidence=0.9, resolved_query=message,
        change_requests=[ChangeRequest(resource="workout_plan", operation="update")],
        plan_input_gaps=[PlanInputGap(quote=message)],
    )
    out = enforce_plan_completeness(resolution, TaskSnapshot(), message)
    assert out.clarification_required
    assert "每个动作减几组" in out.clarification_question and "当前计划没有修改" in out.clarification_question
    assert "待补的计划要求" not in out.clarification_question
    assert out.missing_slots == ["调整方式（减组数、减次数、减天数或缩短时长）"]


def test_named_field_gap_keeps_specific_wording():
    from app.schemas.agent_task import PlanInputGap, TaskSnapshot
    from app.services.agent_intent import ChangeRequest, IntentResolution
    from app.services.agent_plan_completeness import enforce_plan_completeness

    message = "深蹲组数待我补，先别出提案"
    resolution = IntentResolution(
        primary_intent="plan_query", intent_domain="workout_plan", request_kind="mutation",
        requested_effect="update", confidence=0.9, resolved_query=message,
        change_requests=[ChangeRequest(resource="workout_plan", operation="update", field_path="exercise.sets", target_reference="深蹲")],
        plan_input_gaps=[PlanInputGap(quote=message, field_path="exercise.sets", target_reference="深蹲")],
    )
    out = enforce_plan_completeness(resolution, TaskSnapshot(), message)
    assert out.clarification_question.startswith("已保留明确的调整目标。请补充深蹲组数")
