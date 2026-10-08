"""Round09 first-attempt failures, checked independently of model self-report."""
import pytest
from app.schemas.agent_task import ConversationTask, TaskSnapshot, TaskRequirement, TaskUpdate, PendingPlanChange
from app.services.agent_intent import ChangeRequest, resolve_intent
from app.services.agent_task_state import advance_task_state, preserve_pending_plan_changes

def pending_context():
    task=ConversationTask(id='task',last_run_id='run',request='卧推4×7、深蹲2×9',proposal_id='proposal',proposal_pending=True,
        requirements=[TaskRequirement(key='squat',quote='深蹲2组9次',source_run_id='run')],
        pending_plan_changes=[PendingPlanChange(field_path='exercise.'+field,target_reference=target,value=value)
            for target,field,value in [('卧推','sets',4),('卧推','reps','7'),('深蹲','sets',2),('深蹲','reps','9')]])
    return [{'role':'task_state','content':TaskSnapshot(active_task_id='task',tasks=[task]).model_dump_json()}]

def test_revision_keeps_other_pending_exercises():
    edit=TaskUpdate(action='continue',task_id='task',trigger='只把卧推改5组')
    change=ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.sets',target_reference='卧推',value=5)
    result=preserve_pending_plan_changes(pending_context(),edit,[change])
    assert {(c.target_reference,c.field_path):c.value for c in result}=={
        ('卧推','exercise.sets'):5,('卧推','exercise.reps'):'7',('深蹲','exercise.sets'):2,('深蹲','exercise.reps'):'9'}

def test_unchanged_quote_cannot_delete_requirement():
    from app.services.agent_task_state import state_from_context
    edit=TaskUpdate(action='continue',task_id='task',trigger='深蹲和所有次数都不变',requirements=[{'key':'squat','quote':'深蹲和所有次数都不变','remove':True}])
    result=advance_task_state(state_from_context(pending_context()),edit,message=edit.trigger,run_id='next',normalized_request=edit.trigger)
    assert result.tasks[0].requirements[0].quote=='深蹲2组9次'
    assert result.tasks[0].requirements[0].source_run_id=='run'

@pytest.mark.parametrize('message',[
    '列出2026年9月10日至9月12日的训练记录，只要已完成的。',
    '日期更正为2026年9月14日至9月16日，仍只列已完成的。',
    '查2026年9月14日至9月16日的训练记录，只列已完成。',
    '改查2026年9月21日至9月23日，同样只列已完成。',
])
def test_completed_status_is_read(message):
    assert resolve_intent(message).request_kind=='query'

@pytest.mark.parametrize('message',[
    '完成这次训练并保存','把这次训练标记为已完成','把本次训练标记已完成',
    '查已完成的训练，再删除昨天的记录','查询已完成的训练并保存今天的体重65公斤',
])
def test_status_description_never_hides_independent_write(message):
    assert resolve_intent(message).request_kind=='mutation'

@pytest.mark.parametrize('message', ['查看标记为已完成的训练','查看标记已完成的训练','查未完成的训练记录','不要把本次训练标记为已完成，只查询已完成的场次'])
def test_status_nouns_and_negation_still_read(message):
    assert resolve_intent(message).request_kind=='query'

def test_withdraw_one_field_keeps_other_draft_fields():
    from app.schemas.agent_task import PendingPlanWithdrawal
    edit=TaskUpdate(action='continue',task_id='task',trigger='不再修改深蹲次数')
    withdrawal=PendingPlanWithdrawal(field_path='exercise.reps',target_reference='深蹲',quote=edit.trigger)
    result=preserve_pending_plan_changes(pending_context(),edit,[],withdrawals=[withdrawal],message=edit.trigger)
    assert {(c.target_reference,c.field_path):c.value for c in result}=={
        ('卧推','exercise.sets'):4,('卧推','exercise.reps'):'7',('深蹲','exercise.sets'):2}

@pytest.mark.parametrize('quote,target,error',[
    ('继续处理','深蹲','instruction'),('取消硬拉次数','硬拉','unknown'),
    ('取消深蹲次数','深蹲','current_user'),
])
def test_withdrawals_cannot_invent_instruction_or_target(quote,target,error):
    from app.schemas.agent_task import PendingPlanWithdrawal
    edit=TaskUpdate(action='continue',task_id='task',trigger='取消硬拉次数')
    with pytest.raises(ValueError,match=error):
        preserve_pending_plan_changes(pending_context(),edit,[],withdrawals=[PendingPlanWithdrawal(field_path='exercise.reps',target_reference=target,quote=quote)],message='继续处理，取消硬拉次数')

def test_model_mislabels_preservation_and_overwrite_as_withdrawal():
    from app.schemas.agent_task import PendingPlanWithdrawal
    message='只把卧推组数改成5组，深蹲和所有次数都不变'
    update=TaskUpdate(action='continue',task_id='task',trigger=message,requirements=[{'key':'squat','quote':'深蹲和所有次数都不变','remove':True}])
    change=ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.sets',target_reference='卧推',value=5)
    withdrawals=[PendingPlanWithdrawal(field_path=field,target_reference=target,quote=quote) for target,field,quote in [
        ('卧推','exercise.sets','只把卧推组数改成5组'),('卧推','exercise.reps','所有次数都不变'),
        ('深蹲','exercise.sets','深蹲和所有次数都不变'),('深蹲','exercise.reps','深蹲和所有次数都不变')]]
    result=preserve_pending_plan_changes(pending_context(),update,[change],withdrawals=withdrawals,message=message)
    assert {(c.target_reference,c.field_path):c.value for c in result}=={
        ('卧推','exercise.sets'):5,('卧推','exercise.reps'):'7',('深蹲','exercise.sets'):2,('深蹲','exercise.reps'):'9'}

def test_actual_withdrawal_cannot_be_silently_overwritten():
    from app.schemas.agent_task import PendingPlanWithdrawal
    edit=TaskUpdate(action='continue',task_id='task',trigger='取消深蹲次数修改')
    change=ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.reps',target_reference='深蹲',value='8')
    with pytest.raises(ValueError,match='conflicts'):
        preserve_pending_plan_changes(pending_context(),edit,[change],withdrawals=[PendingPlanWithdrawal(field_path='exercise.reps',target_reference='深蹲',quote=edit.trigger)],message=edit.trigger)

def test_withdrawal_cannot_revive_applied_or_expired_proposal():
    from app.services.agent_task_state import state_from_context
    from app.schemas.agent_task import PendingPlanWithdrawal
    state=state_from_context(pending_context());state.tasks[0].proposal_pending=False
    with pytest.raises(ValueError,match='requires_pending'):
        preserve_pending_plan_changes([{'role':'task_state','content':state.model_dump_json()}],TaskUpdate(action='continue',task_id='task'),[],withdrawals=[PendingPlanWithdrawal(field_path='exercise.reps',target_reference='深蹲',quote='取消深蹲次数')],message='取消深蹲次数')

@pytest.mark.parametrize('pending,phase',[(False,'responded'),(False,'awaiting_confirmation'),(True,'cancelled')])
def test_meal_draft_never_grants_saved_record_update(pending,phase):
    from app.schemas.agent_task import PendingMealDraft
    from app.services.agent_task_state import revise_pending_meal
    task=ConversationTask(id='task',last_run_id='run',request='meal',phase=phase,proposal_pending=pending,
        pending_meal=PendingMealDraft(logged_at='2026-09-18',meal_type='晚餐',items=[{'food_id':'rice','food_name':'米饭','amount_g':160}]))
    context=[{'role':'task_state','content':TaskSnapshot(active_task_id='task',tasks=[task]).model_dump_json()}]
    change=ChangeRequest(resource='nutrition',operation='update',field_path='meal.meal_type',value='午餐')
    assert revise_pending_meal(context,TaskUpdate(action='continue',task_id='task'),[change])==[change]

def test_meal_revision_keeps_date_and_replaces_items_without_resurrecting_removed_food():
    from app.services.agent_task_state import _pending_meal,revise_pending_meal
    meal=_pending_meal({'proposal_type':'meal_log_create_v1','after':{'logged_at':'2026-09-19','meal_type':'午餐','items':[
        {'food_id':'rice','food_name':'米饭','amount_g':170,'calories':221},
        {'food_id':'chicken','food_name':'鸡胸','amount_g':80,'calories':132}]}})
    assert 'calories' not in meal.model_dump_json()
    task=ConversationTask(id='task',last_run_id='run',request='meal',proposal_pending=True,pending_meal=meal)
    context=[{'role':'task_state','content':TaskSnapshot(active_task_id='task',tasks=[task]).model_dump_json()}]
    change=ChangeRequest(resource='nutrition',operation='update',field_path='meal.items',value=[{'food_id':'rice','amount_g':170}])
    result=revise_pending_meal(context,TaskUpdate(action='continue',task_id='task'),[change])[0]
    assert result.operation=='create' and result.target_reference is None
    assert result.value=={'logged_at':'2026-09-19','meal_type':'午餐','items':[{'food_id':'rice','amount_g':170}]}

@pytest.mark.parametrize('action',['new','none','cancel'])
def test_new_or_cancelled_task_never_inherits_pending_fields(action):
    change=ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.sets',target_reference='卧推',value=5)
    assert preserve_pending_plan_changes(pending_context(),TaskUpdate(action=action),[change])==[change]
