import json
from datetime import date

import pytest

from app.schemas.agent_task import TaskUpdate
from app.services.agent_task_state import (
    advance_task_state, execution_task_context, finalize_task_snapshot,
    is_read_only_revision, state_from_context,
)
from app.services.business_clock import BUSINESS_DATE
from app.services.agent_intent import resolve_intent
from app.services.agent_intent_model import _intent_user_content


def update(action, task_id="", trigger="继续", **requirements):
    return TaskUpdate(action=action, task_id=task_id, trigger=trigger,
                      requirements=[{"key": key, "quote": value} for key, value in requirements.items()])


def initial():
    message = "训练建议最多40分钟，周三不能练，只看建议不要保存"
    return advance_task_state(None, update("new", trigger=message, duration="最多40分钟",
                                          excluded_days="周三不能练", permission="只看建议不要保存"),
                              message=message, run_id="first", normalized_request=message)


def test_old_requirements_survive_window_and_latest_value_replaces():
    old = initial()
    state = advance_task_state(old, update("continue", "first", trigger="改为30分钟", duration="30分钟"),
                               message="改为30分钟", run_id="second", normalized_request="训练建议30分钟，周三不能练")
    context = [{"role": "task_state", "content": state.model_dump_json()}] + [
        {"role": "assistant", "content": "占位" * 500} for _ in range(30)]
    prompt = _intent_user_content("按这些要求", context_messages=context, pending_clarification=None, repair_error=None)
    assert "周三不能练" in prompt and "30分钟" in prompt
    assert "40分钟" not in prompt
    assert old.tasks[0].requirements[0].quote == "最多40分钟"  # immutable input
    assert state.tasks[0].requirements[0].source_run_id == "second"
    assert state.tasks[0].requirements[1].source_run_id == "first"


def test_switch_resume_cancel_and_new_do_not_leak_constraints():
    state = initial()
    state = advance_task_state(state, update("new", trigger="解释蛋白质"), message="解释蛋白质",
                               run_id="topic", normalized_request="解释蛋白质")
    assert "周三" not in execution_task_context(state)
    state = advance_task_state(state, update("resume", "first", trigger="回到训练建议"),
                               message="回到训练建议", run_id="resume", normalized_request="训练建议")
    assert "周三" in execution_task_context(state)
    state = advance_task_state(state, update("cancel", "first", trigger="取消"), message="取消",
                               run_id="cancel", normalized_request="取消")
    assert state.active_task_id is None and execution_task_context(state) == ""
    with pytest.raises(ValueError, match="unavailable"):
        advance_task_state(state, update("resume", "first"), message="继续", run_id="bad", normalized_request="继续")


@pytest.mark.parametrize("patch,message,error", [
    (update("continue", "missing"), "继续", "unavailable"),
    (update("continue", "first", duration="20分钟"), "继续", "current_user_quote"),
    (update("continue", "first", trigger="助手说"), "继续", "current_user_quote"),
    (TaskUpdate(action="none", requirements=[{"key":"x", "quote":"继续"}]), "继续", "none_has_edits"),
    (TaskUpdate(action="continue", task_id="first", trigger="继续", requirements=[{"key":"x","quote":"继续","remove":True}]), "继续", "unknown_requirement"),
    (TaskUpdate(action="new", task_id="first", trigger="继续"), "继续", "new_cannot_reference"),
    (TaskUpdate(action="continue", task_id="first", trigger="继续", requirements=[{"key":"x","quote":"继续"}]*2), "继续", "duplicate"),
])
def test_invalid_or_unattributed_updates_rejected(patch, message, error):
    with pytest.raises(ValueError, match=error):
        advance_task_state(initial(), patch, message=message, run_id="next", normalized_request=message)


def test_explicit_removal_is_source_checked_and_requirement_limit_fails_closed():
    patch = TaskUpdate(action="continue", task_id="first", trigger="取消时长限制",
                       requirements=[{"key":"duration","quote":"取消时长限制","remove":True}])
    state = advance_task_state(initial(), patch, message="取消时长限制", run_id="next", normalized_request="训练建议")
    assert "40分钟" not in execution_task_context(state)
    patch = TaskUpdate(action="continue", task_id="first", trigger="继续",
                       requirements=[{"key":f"new{i}","quote":"继续"} for i in range(12)])
    with pytest.raises(ValueError, match="capacity"):
        advance_task_state(initial(), patch, message="继续", run_id="next", normalized_request="继续")


def test_bounded_tasks_record_eviction_and_forbid_implicit_revival():
    state = initial()
    for i in range(3):
        state = advance_task_state(state, update("new"), message="继续", run_id=str(i), normalized_request="新任务")
    assert len(state.tasks) == 3 and state.evicted_task_ids == ["first"]
    with pytest.raises(ValueError):
        advance_task_state(state, update("resume", "first"), message="继续", run_id="bad", normalized_request="继续")


def test_source_date_is_frozen_per_requirement():
    token = BUSINESS_DATE.set(date(2026,9,29))
    try:
        state = initial()
    finally:
        BUSINESS_DATE.reset(token)
    assert all(item.as_of == "2026-09-29" for item in state.tasks[0].requirements)


@pytest.mark.parametrize("terminal,phase", [("answer","responded"),("clarify","awaiting_input"),("proposal","awaiting_confirmation"),("failed","failed"),("safe_stop","blocked")])
def test_response_never_claims_business_completion(terminal, phase):
    state = finalize_task_snapshot(initial(), terminal_action=terminal, content_data={"artifact":{"id":"a"},"proposal":{"id":"p"}})
    assert state.tasks[0].phase == phase
    assert state.tasks[0].artifact_id == "a" and state.tasks[0].proposal_id == "p"


def test_untracked_legacy_route_never_injects_old_task_into_new_execution():
    state = advance_task_state(initial(), None, message="查询饮食", run_id="legacy", normalized_request="查询饮食")
    assert execution_task_context(state) == ""
    assert state_from_context([{"role":"assistant","content":json.dumps({"tasks":[]})}]).tasks == []
    assert state_from_context([{"role":"task_state","content":"bad json"}]).tasks == []


def test_advice_revision_exception_never_applies_to_explicit_writes():
    context = [{"role":"task_state","content":initial().model_dump_json()}]
    patch = update("continue", "first")
    assert is_read_only_revision(context, patch, "改为30分钟")
    assert not is_read_only_revision(context, patch, "保存为30分钟")
    assert not is_read_only_revision(context, patch, "删除旧计划")


@pytest.mark.parametrize("message,kind", [
    ("查9月已完成的场次", "query"), ("完成这次训练并保存", "mutation"),
    ("把卧推改为4组", "mutation"), ("查已完成的训练记录", "query"),
])
def test_completed_description_does_not_hide_mutation(message, kind):
    assert resolve_intent(message).request_kind == kind


@pytest.mark.asyncio
async def test_invalid_requirement_source_reports_specific_repair_code(monkeypatch):
    from app.services import agent_intent_model as model
    route = model.IntentRouteDecision(intent_domain="general",request_kind="query",requested_effect="read",
        requested_output="answer",read_targets=[],decision_action=None,artifact_action=None,
        normalized_request="训练建议",risk_level="low",confidence=.9,
        task_update=TaskUpdate(action="continue",task_id="first",trigger="去掉时间限制",
            requirements=[{"key":"duration","quote":"最多40分钟","remove":True}]))
    async def invoke(*args,**kwargs): return route
    monkeypatch.setattr(model,"_invoke_model_route",invoke)
    with pytest.raises(model.IntentStructuredOutputError,match="task_requirement_not_current_user_quote"):
        await model._invoke_model_intent("去掉时间限制",context_messages=[{"role":"task_state","content":initial().model_dump_json()}])


@pytest.mark.asyncio
async def test_verified_query_and_pending_cancel_update_task_state():
    from app.services.agent_intent_model import resolve_intent_with_fallback
    state=initial()
    context=[{"role":"task_state","content":state.model_dump_json()},
             {"role":"assistant","content":"本周统计", "query_context":{
                 "source_run_id":"first","primary_intent":"workout_progress_query","resolved_query":"查询本周训练统计",
                 "request_kind":"query","requested_effect":"read","risk_level":"low","successful_query":True}}]
    query=await resolve_intent_with_fallback("那上周呢",context_messages=context,use_model=False)
    assert query.resolution.task_update.action=="continue"
    assert query.resolution.task_update.requirements[0].key=="date_scope"
    cancelled=await resolve_intent_with_fallback("取消",context_messages=context,
        pending_clarification={"origin_run_id":"first","primary_intent":"general_qa","request_kind":"query","missing_slots":["动作"]},use_model=False)
    assert cancelled.resolution.task_update.action=="cancel"


@pytest.mark.parametrize("pending,target,preserve,remove,count", [
    (True,"卧推",True,False,2), (False,"卧推",True,False,1),
    (True,"深蹲",True,False,3), (True,"卧推",False,False,1), (True,"卧推",True,True,None),
])
def test_unconfirmed_revision_preserves_entire_draft_and_requires_explicit_withdrawals(pending,target,preserve,remove,count):
    from app.services.agent_intent import ChangeRequest
    from app.services.agent_task_state import preserve_pending_plan_changes
    from app.schemas.agent_task import PendingPlanChange
    state=initial()
    state.tasks[0].proposal_pending=pending
    state.tasks[0].pending_plan_changes=[PendingPlanChange(field_path="exercise.sets",target_reference="卧推",value=4),
                                       PendingPlanChange(field_path="exercise.reps",target_reference="卧推",value="8")]
    patch=update("continue","first")
    if remove:
        patch=TaskUpdate(action="continue",task_id="first",trigger="不再改次数",
                         requirements=[{"key":"reps","quote":"不再改次数","remove":True}])
    changes=[ChangeRequest(resource="workout_plan",operation="update",field_path="exercise.sets",target_reference=target,value=5,preserve_unspecified=preserve)]
    if count is None:
        with pytest.raises(ValueError,match="withdrawal_fields_required"):
            preserve_pending_plan_changes([{"role":"task_state","content":state.model_dump_json()}],patch,changes)
        return
    result=preserve_pending_plan_changes([{"role":"task_state","content":state.model_dump_json()}],patch,changes)
    assert len(result)==count
    assert next(item.value for item in result if item.field_path=="exercise.sets" and item.target_reference==target)==5
    if count==2:
        assert next(item.value for item in result if item.field_path=="exercise.reps")=="8"
