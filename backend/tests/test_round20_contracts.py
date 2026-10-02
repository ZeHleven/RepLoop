import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.services.history_status_scope import parse_history_status_scope
from app.services import agent_intent_model as im
from app.services.ai_client import StructuredCompletionResult
from app.services.agent_intent import ChangeRequest
from app.schemas.agent_task import ConversationTask,TaskSnapshot,TaskUpdate
from app.services.agent_task_state import preserve_pending_plan_changes,advance_task_state

@pytest.mark.parametrize('text,expected',[
 ('除了跳过和放弃，其余状态都列出来',('completed','in_progress','ended_early')),
 ('除跳过和放弃之外的所有状态（含已完成、进行中、提前结束）',('completed','in_progress','ended_early')),
 ('提前结束的也不看，已完成和进行中的保留',('completed','in_progress')),
 ('我指的是提前结束和放弃这两类，不包括其他状态',('ended_early','abandoned')),
 ('放弃的先不列了，只保留提前结束',('ended_early',)),
 ('跳过和放弃都不要，其他状态都要',('completed','in_progress','ended_early')),
 ('仅包含已完成和进行中，除此之外都排除',('completed','in_progress')),
 ('不查跳过、放弃，其他全部状态都要',('completed','in_progress','ended_early')),
 ('已完成的不看，提前结束的也不看，只看进行中',('in_progress',)),
 ('除了已完成，其他状态不要',('completed',)),
 ('所有状态，不看跳过',('completed','in_progress','ended_early','abandoned')),
 ('只看已完成，不看已完成',None),
 ('不是不看跳过',None),('除了跳过之外也不排除跳过',None),
 ('不怎么想看进行中',None),('其他状态',None),('没练完的',None),
])
def test_whole_clause_status_operations(text,expected):
    result=parse_history_status_scope(text)
    assert result.complete is (expected is not None)
    assert result.statuses==expected

def raw_fixture():
    return json.loads((Path(__file__).parent/'fixtures/round19_j01.json').read_text(encoding='utf-8'))

def completion(row):
    return StructuredCompletionResult(payload=row['payload'],raw_output=row['raw'],mode=row['mode'],finish_reason='stop',duration_ms=0,output_chars=len(row['raw']))

@pytest.mark.asyncio
async def test_original_pending_information_cannot_make_partial_proposal(monkeypatch):
    data=raw_fixture()
    monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(side_effect=[completion(x) for x in data['model_outputs'][:2]]))
    invocation=await im._invoke_model_intent(data['turns'][0]['message'],timeout_seconds=30)
    assert invocation.resolution.clarification_required
    assert len(invocation.resolution.change_requests)==8
    assert invocation.resolution.missing_slots

def test_workflow_release_is_not_business_field_withdrawal():
    data=raw_fixture();state=TaskSnapshot.model_validate(data['runs'][0]['trace']['task_state'])
    state.tasks[0].proposal_pending=True
    route=im.IntentRouteDecision.model_validate(data['model_outputs'][2]['payload'])
    changes=[ChangeRequest(resource='workout_plan',operation='update',field_path='schedule.duration_weeks',value=6)]
    result=preserve_pending_plan_changes([{'role':'task_state','content':state.model_dump_json()}],route.task_update,changes,message=data['turns'][1]['message'])
    assert len(result)==9

@pytest.mark.asyncio
async def test_original_reset_pair_becomes_one_sourced_edit(monkeypatch):
    data=raw_fixture();state=TaskSnapshot.model_validate(data['runs'][0]['trace']['task_state']);state.tasks[0].proposal_pending=True
    route=im.IntentRouteDecision.model_validate(data['model_outputs'][-2]['payload'])
    resolution=im._route_to_resolution(route).model_copy(update={'change_requests':[ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.rest_seconds',target_reference='卧推',value=90)]})
    monkeypatch.setattr(im,'_invoke_model_route',AsyncMock(return_value=route))
    monkeypatch.setattr(im,'_invoke_model_change_extraction',AsyncMock(return_value=resolution))
    result=await im._invoke_model_intent(data['turns'][2]['message'],context_messages=[{'role':'task_state','content':state.model_dump_json()}],timeout_seconds=30)
    assert len({e.key for e in result.resolution.task_update.requirements})==len(result.resolution.task_update.requirements)
    assert next(c.value for c in result.resolution.change_requests if c.field_path=='exercise.rest_seconds' and c.target_reference=='卧推')==90

@pytest.mark.parametrize('quotes',[['恢复90秒','恢复100秒'],['恢复90秒','恢复90秒']])
def test_arbitrary_duplicate_edits_still_rejected(quotes):
    update=TaskUpdate(action='new',trigger='恢复90秒恢复100秒',requirements=[{'key':'rest','quote':q} for q in quotes])
    with pytest.raises(ValueError,match='task_duplicate_requirement_key'):
        advance_task_state({},update,message=update.trigger,run_id='r',normalized_request=update.trigger)

from app.services.agent_plan_completeness import enforce_plan_completeness,workflow_release
from app.services.agent_intent import IntentResolution

def waiting_plan():
    task=ConversationTask(id='plan',request='当前训练计划，周期待补',last_run_id='old',phase='awaiting_input',plan_input_gaps=[{'field_path':'schedule.duration_weeks','quote':'周期待补'}])
    return TaskSnapshot(active_task_id='plan',tasks=[task],transition='continue')

@pytest.mark.parametrize('message,value,blocked',[
    ('周期定为6周',6,False),('卧推6次，周期仍待补',6,True),
    ('其他不变，卧推改6次',6,True),('周期定为16周',6,True),
])
def test_gap_requires_corresponding_explicit_value(message,value,blocked):
    state=waiting_plan()
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',confidence=.9,change_requests=[ChangeRequest(resource='workout_plan',operation='update',field_path='schedule.duration_weeks',value=value)])
    result=enforce_plan_completeness(r,state,message)
    assert result.clarification_required is blocked
    assert bool(state.tasks[0].plan_input_gaps) is blocked

def test_missing_goal_survives_model_omission_and_false_source_is_rejected():
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',confidence=.9,change_requests=[])
    assert enforce_plan_completeness(r,waiting_plan(),'其他不变').clarification_required
    r=r.model_copy(update={'plan_input_gaps':[__import__('app.schemas.agent_task',fromlist=['PlanInputGap']).PlanInputGap(field_path='exercise.sets',quote='not user text')]})
    with pytest.raises(ValueError,match='gap_not_user_quote'):enforce_plan_completeness(r,waiting_plan(),'继续')

def test_new_task_does_not_inherit_pending_goal():
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',confidence=.9)
    state=waiting_plan();state.transition='new'
    assert not enforce_plan_completeness(r,state,'新任务').clarification_required
    assert not state.tasks[0].plan_input_gaps

def test_workflow_release_cannot_erase_business_goal_or_negated_release():
    assert workflow_release('信息补齐再出完整提案','现在生成完整提案，先不要应用')
    assert not workflow_release('卧推休息80秒','现在生成完整提案')
    assert not workflow_release('信息补齐再出完整提案','不要现在生成完整提案')

def test_business_requirement_removal_still_needs_field_evidence():
    data=raw_fixture();state=TaskSnapshot.model_validate(data['runs'][0]['trace']['task_state']);state.tasks[0].proposal_pending=True
    update=TaskUpdate(action='continue',task_id=state.active_task_id,trigger='撤回卧推组数',requirements=[{'key':'bench_sets','quote':'撤回卧推组数','remove':True}])
    with pytest.raises(ValueError,match='withdrawal_fields_required'):
        preserve_pending_plan_changes([{'role':'task_state','content':state.model_dump_json()}],update,[],message=update.trigger)

import pytest_asyncio
from uuid import uuid4
from sqlalchemy import select
from evals.postgres_test_fixtures import engine,session_factory,db_session
from evals.task_fixtures import task_client

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
async def test_runtime_blocks_partial_plan_and_retains_goals_until_confirmed(task_client,db_session,session_factory,monkeypatch):
    from evals.tests.test_agent_tasks import seed
    from app.models.exercise import Exercise
    from app.models.workout import WorkoutPlan,PlannedExercise
    from app.models.agent import AgentConversation,AgentProposal
    from app.services import agent_runtime
    from app.services.agent_intent import IntentResolverOutcome
    from app.services.agent_jobs import claim_next_agent_run,process_agent_run
    from app.services.auth import create_access_token
    from app.config import settings
    user,profile,conv,foods=await seed(db_session);cid=conv.id;uid=user.id
    plan=WorkoutPlan(user_id=uid,name='合成完整性计划',days_per_week=1,duration_weeks=4)
    ex=Exercise(name_zh='卧推',name_en='Synthetic',category='strength',difficulty='beginner',muscle_primary=['chest'])
    db_session.add_all([plan,ex]);await db_session.flush();pid=plan.id
    row=PlannedExercise(plan_id=pid,exercise_id=ex.id,day_of_week=1,sets=3,reps='10',rest_seconds=90,order_index=0)
    db_session.add(row);await db_session.commit();rowid=row.id
    monkeypatch.setattr(settings,'AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED',True)
    message='当前计划卧推4组9次，休息95秒；周期待我补充。'
    initial=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',change_requests=[ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.'+field,target_reference='卧推',value=value) for field,value in [('sets',4),('reps','9'),('rest_seconds',95)]],resolved_query=message,confidence=.95,clarification_required=False,task_update=TaskUpdate(action='new',task_id='',trigger=message,requirements=[{'key':'bench','quote':'卧推4组9次'},{'key':'rest','quote':'休息95秒'}]))
    monkeypatch.setattr(agent_runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(initial,'model')))
    headers={'Authorization':'Bearer '+create_access_token(uid)}
    async def submit(text):
        resp=await task_client.post('/api/v1/agent/runs',headers=headers,json={'conversation_id':cid,'message':text,'client_request_id':uuid4().hex});assert resp.status_code==202
        rid=resp.json()['run_id']
        async with session_factory() as worker:assert await claim_next_agent_run(worker)==rid
        await process_agent_run(session_factory,rid)
        return (await task_client.get('/api/v1/agent/runs/'+rid,headers=headers)).json()
    first=await submit(message);assert first['clarification_required'] and not first.get('proposal')
    db_session.expire_all();conv=await db_session.get(AgentConversation,cid)
    assert conv.pending_clarification
    state=TaskSnapshot.model_validate(first['execution_trace']['task_state'])
    task=next(t for t in state.tasks if t.id==state.active_task_id)
    assert len(task.unproposed_plan_changes)==3
    assert task.plan_input_gaps[0].field_path=="schedule.duration_weeks"
    second_message='周期6周，其余要求保留。'
    route=im.IntentRouteDecision(intent_domain='workout_plan',request_kind='mutation',requested_effect='update',requested_output='answer',read_targets=[],decision_action=None,artifact_action=None,normalized_request='当前计划周期6周，保留卧推4组9次休息95秒',risk_level='low',confidence=.95,task_update=TaskUpdate(action='continue',task_id=task.id,trigger=second_message))
    partial=im._route_to_resolution(route).model_copy(update={'change_requests':[ChangeRequest(resource='workout_plan',operation='update',field_path='schedule.duration_weeks',value=6)]})
    monkeypatch.setattr(im,'_invoke_model_route',AsyncMock(return_value=route))
    monkeypatch.setattr(im,'_invoke_model_change_extraction',AsyncMock(return_value=partial))
    async def resolve(message,**kw):
        invocation=await im._invoke_model_intent(message,context_messages=kw.get('context_messages'),pending_clarification=kw.get('pending_clarification'),timeout_seconds=30)
        return IntentResolverOutcome(invocation.resolution,'model')
    monkeypatch.setattr(agent_runtime,'resolve_intent_with_fallback',resolve)
    second=await submit(second_message);assert not second['clarification_required'] and second.get('proposal')
    db_session.expire_all();unchanged=await db_session.get(PlannedExercise,rowid)
    assert (unchanged.sets,unchanged.reps,unchanged.rest_seconds)==(3,'10',90)
    ref=second['proposal'];confirmed=await task_client.post('/api/v1/proposals/'+ref['id']+'/confirm',headers=headers,json={'expected_version':ref['version'],'client_request_id':uuid4().hex});assert confirmed.status_code==200
    db_session.expire_all();finalplan=await db_session.scalar(select(WorkoutPlan).where(WorkoutPlan.user_id==uid,WorkoutPlan.is_active.is_(True)))
    final=await db_session.scalar(select(PlannedExercise).where(PlannedExercise.plan_id==finalplan.id))
    assert (final.sets,final.reps,final.rest_seconds,finalplan.duration_weeks)==(4,'9',95,6)


def test_unpunctuated_next_prefix_is_not_previous_suffix():
    assert parse_history_status_scope('只看已完成不看跳过').statuses==('completed',)

def test_withdraw_missing_field_preserves_known_unproposed_goals():
    from app.schemas.agent_task import PendingPlanWithdrawal,PendingPlanChange
    state=waiting_plan();state.tasks[0].unproposed_plan_changes=[PendingPlanChange(field_path='exercise.sets',target_reference='卧推',value=4)]
    message='周期不改了，撤回周期调整，卧推4组保留'
    withdrawal=PendingPlanWithdrawal(field_path='schedule.duration_weeks',quote='撤回周期调整')
    update=TaskUpdate(action='continue',task_id='plan',trigger=message)
    changes=preserve_pending_plan_changes([{'role':'task_state','content':state.model_dump_json()}],update,[],withdrawals=[withdrawal],message=message)
    assert len(changes)==1 and changes[0].value==4
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',confidence=.9,change_requests=changes,pending_plan_withdrawals=[withdrawal])
    assert not enforce_plan_completeness(r,state,message).clarification_required

@pytest.mark.parametrize('phase,pending',[('cancelled',False),('awaiting_confirmation',False)])
def test_cancelled_or_expired_proposal_does_not_carry_gap(phase,pending):
    state=waiting_plan();state.tasks[0].phase=phase;state.tasks[0].proposal_id='expired';state.tasks[0].proposal_pending=pending
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',confidence=.9)
    assert not enforce_plan_completeness(r,state,'调整卧推4组').clarification_required


@pytest.mark.asyncio
@pytest.mark.parametrize('name,valid',[('鸡胸肉',False),('鸡胸',True)])
async def test_original_food_name_response_rejects_model_synonym(monkeypatch,name,valid):
    data=json.loads((Path(__file__).parent/'fixtures/round20_j03_food_name.json').read_text(encoding='utf-8'))
    route=im.IntentRouteDecision.model_validate(data['route']['payload'])
    row=data['extraction'];value=json.loads(row['payload']['change_requests'][0]['value_json'])
    value['items'][1]['food_name']=name
    row['payload']['change_requests'][0]['value_json']=json.dumps(value,ensure_ascii=False)
    monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(return_value=completion(row)))
    if valid:
        result,_=await im._invoke_model_change_extraction(data['message'],route=route,timeout_seconds=30)
        assert result.change_requests[0].value['items'][1]['food_name']=='鸡胸'
    else:
        with pytest.raises(im.IntentStructuredOutputError,match='mutation_food_name_not_source'):
            await im._invoke_model_change_extraction(data['message'],route=route,timeout_seconds=30)

def test_food_name_has_no_authority_from_assistant_or_other_task():
    with pytest.raises(im.IntentStructuredOutputError,match='food_name_not_source'):
        im._validate_meal_name_sources({'items':[{'food_name':'未知食物','amount_g':10}]},'记录米饭',[{'role':'assistant','content':'未知食物'}],None)


@pytest.mark.parametrize('message',[
 '接着刚才那份还没确认的训练计划，把深蹲重量改为35公斤，其他所有修改照旧。',
 '把深蹲重量改为35公斤，稍后确认提案。',
 '先把周期改为6周，下一句再确认。',
])
def test_pending_approval_is_not_missing_input(message):
    changes=[ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.recommended_weight_kg',target_reference='深蹲',value=35),ChangeRequest(resource='workout_plan',operation='update',field_path='schedule.duration_weeks',value=6)]
    r=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',change_requests=changes,confidence=.9)
    assert not enforce_plan_completeness(r,TaskSnapshot(),message).clarification_required

@pytest.mark.asyncio
async def test_actual_return_to_unconfirmed_plan_keeps_full_draft(monkeypatch):
    data=json.loads((Path(__file__).parent/'fixtures/round20_g01_waiting.json').read_text(encoding='utf-8'))
    state=TaskSnapshot.model_validate(data['runs'][1]['trace']['task_state'])
    for task in state.tasks:
        if task.proposal_id:task.proposal_pending=True
    monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(side_effect=[completion(x) for x in data['model_outputs'][-2:]]))
    result=await im._invoke_model_intent(data['turns'][-1]['message'],context_messages=[{'role':'task_state','content':state.model_dump_json()}],timeout_seconds=30)
    assert not result.resolution.clarification_required
    assert {(c.target_reference,c.field_path,c.value) for c in result.resolution.change_requests} >= {('卧推','exercise.sets',4),('卧推','exercise.reps','7'),('深蹲','exercise.recommended_weight_kg',35)}


@pytest.mark.parametrize('left,right',[('“','”'),('‘','’'),('"','"'),("'","'")])
def test_quoted_status_operands_keep_exclusion_and_conflict(left,right):
    quote=lambda s:left+s+right
    assert parse_history_status_scope('仅保留'+quote('放弃')+'（不再包含'+quote('跳过')+'）').statuses==('abandoned',)
    assert parse_history_status_scope('除了'+quote('跳过')+'和'+quote('放弃')+'其余状态都要').statuses==('completed','in_progress','ended_early')
    assert not parse_history_status_scope('不是不看'+quote('跳过')).complete
    assert not parse_history_status_scope('只看'+quote('已完成')+'，不看'+quote('已完成')).complete


def test_original_h04_quoted_model_resolution_agrees_with_user():
    from app.services.history_status_scope import history_status_problem
    data=json.loads((Path(__file__).parent/'fixtures/round20_h04_quotes.json').read_text(encoding='utf-8'))
    turn=data['turns'][2]
    resolution=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',confidence=.95,resolved_query=turn['body']['resolved_query'])
    assert not history_status_problem(resolution,turn['message'])
    assert parse_history_status_scope(resolution.resolved_query).statuses==('abandoned',)
