import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.services.history_status_scope import parse_history_status_scope
from app.services.agent_intent import ChangeRequest,IntentResolution
from app.schemas.agent_task import TaskSnapshot,ConversationTask,TaskUpdate
from app.services.agent_task_state import finalize_task_snapshot,preserve_pending_plan_changes,is_calendar_query_revision
from app.services import agent_intent_model as im
from app.services.ai_client import StructuredCompletionResult

@pytest.mark.parametrize('text,expected',[
 ('排除已完成和进行中，其他状态都要',('ended_early','skipped','abandoned')),
 ('只保留放弃（不再包含跳过）',('abandoned',)),
 ('只保留已完成与进行中，不列出跳过、放弃和提前结束',('completed','in_progress')),
 ('所有状态，但排除已完成',('in_progress','ended_early','skipped','abandoned')),
 ('只查已完成，不查已完成',None),('不怎么想看进行中',None),('其他状态',None),
 ('未完成',None),('只查进行中和提前结束（未完成）',('in_progress','ended_early')),
])
def test_status_contract(text,expected):
    scope=parse_history_status_scope(text)
    assert scope.complete is (expected is not None)
    assert scope.statuses==expected

def context(phase='awaiting_input',changes=None):
    task=ConversationTask(id='plan',request='当前计划卧推4组9次，休息95秒；深蹲2组15次',phase=phase,last_run_id='old',requirements=[{'key':'bench_sets','quote':'卧推4组9次','source_run_id':'old'}])
    state=TaskSnapshot(active_task_id='plan',tasks=[task],transition='continue')
    if changes is not None:state=finalize_task_snapshot(state,terminal_action='clarify',changes=changes)
    return [{'role':'task_state','content':state.model_dump_json()}]

def change(field,value,target='卧推'):
    return ChangeRequest(resource='workout_plan',operation='update',field_path='exercise.'+field,target_reference=target,value=value)

def test_unproposed_structured_goals_survive_clarification():
    ctx=context(changes=[change('sets',4),change('reps','9'),change('rest_seconds',95)])
    result=preserve_pending_plan_changes(ctx,TaskUpdate(action='continue',task_id='plan',trigger='改11次'),[change('reps','11')],message='改11次')
    assert {c.field_path:c.value for c in result}=={'exercise.sets':4,'exercise.reps':'11','exercise.rest_seconds':95}

def test_unknown_old_plan_goals_cannot_be_silently_dropped():
    with pytest.raises(ValueError,match='task_plan_unresolved_requirements'):
        preserve_pending_plan_changes(context(),TaskUpdate(action='continue',task_id='plan',trigger='改11次'),[change('reps','11')],message='改11次')

@pytest.mark.parametrize('message,ok',[
 ('同一日期范围，改为只显示跳过和放弃的记录。',True),
 ('日期收窄到2026年9月19日，刚选的两种状态保留。',True),
 ('只显示跳过和放弃的记录，然后删除已完成记录',False),
 ('把当前训练状态改为已完成',False),('只显示记录，同时保存这次训练',False),
])
def test_read_scope_revision_does_not_require_previous_success(message,ok):
    state=TaskSnapshot(active_task_id='q',tasks=[ConversationTask(id='q',request='查询2026年9月14日至20日的训练记录',last_run_id='q',phase='awaiting_input')])
    r=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',resolved_query='查询训练记录',confidence=.95,task_update=TaskUpdate(action='continue',task_id='q',trigger=message))
    assert is_calendar_query_revision([{'role':'task_state','content':state.model_dump_json()}],r,message) is ok

@pytest.mark.asyncio
async def test_original_create_label_on_current_plan_is_update(monkeypatch):
    fixture=json.loads((Path(__file__).parent/'fixtures/round17_h05_first.json').read_text(encoding='utf-8'))
    completions=[StructuredCompletionResult(payload=m['payload'],raw_output=m['raw'],mode=m['mode'],finish_reason='stop',duration_ms=0,output_chars=len(m['raw'])) for m in fixture['model_outputs'][:2]]
    monkeypatch.setattr(im,'structured_chat_completion',AsyncMock(side_effect=completions))
    result=await im._invoke_model_intent(fixture['message'],timeout_seconds=30)
    assert result.resolution.requested_effect=='update'
    assert len(result.resolution.change_requests)==8 and all(c.operation=='update' for c in result.resolution.change_requests)

@pytest.mark.asyncio
async def test_dated_active_query_uses_history_target(monkeypatch):
    route=im.IntentRouteDecision(intent_domain='workout_session',request_kind='query',requested_effect='read',requested_output='answer',read_targets=['active_workout_session'],decision_action=None,artifact_action=None,normalized_request='查询2026年9月20日进行中的训练',risk_level='low',confidence=.95)
    monkeypatch.setattr(im,'_invoke_model_route',AsyncMock(return_value=route))
    out=await im._invoke_model_intent('查2026年9月20日进行中的训练',timeout_seconds=30)
    assert out.resolution.intent_domain=='workout_history'
    assert out.resolution.evidence_requirements==['workout_history']

@pytest.mark.parametrize('message,domain,effect',[
 ('当前计划不合适，请新建4周计划','workout_plan','create'),
 ('复制当前计划为4周副本','workout_plan','create'),
 ('当前正在练什么','workout_session','read'),
 ('查2026年9月20日当前正在练什么','workout_session','read'),
])
def test_target_normalization_keeps_new_plans_and_current_sessions(message,domain,effect):
    route=im.IntentRouteDecision(intent_domain=domain,request_kind='query' if effect=='read' else 'mutation',requested_effect=effect,requested_output='answer',read_targets=['active_workout_session'] if effect=='read' else [],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.9)
    fixed=im._normalize_query_and_plan_target(route,message)
    assert fixed.intent_domain==domain and fixed.requested_effect==effect

def test_cancelled_and_expired_drafts_do_not_supply_unproposed_goals():
    ctx=context(changes=[change('sets',4)])
    state=TaskSnapshot.model_validate_json(ctx[0]['content']);state.tasks[0].phase='cancelled'
    ctx[0]['content']=state.model_dump_json()
    latest=[change('reps','11')]
    assert preserve_pending_plan_changes(ctx,TaskUpdate(action='continue',task_id='plan',trigger='x'),latest)==latest
    state.tasks[0].phase='awaiting_confirmation';state.tasks[0].proposal_id='expired';state.tasks[0].proposal_pending=False
    ctx[0]['content']=state.model_dump_json()
    assert preserve_pending_plan_changes(ctx,TaskUpdate(action='continue',task_id='plan',trigger='x'),latest)==latest

import pytest_asyncio
from uuid import uuid4
from sqlalchemy import select
from evals.postgres_test_fixtures import engine,session_factory,db_session
from evals.task_fixtures import task_client

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
async def test_clarification_without_proposal_preserves_goals_through_confirmation(task_client,db_session,session_factory,monkeypatch):
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
    initial=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='mutation',requested_effect='update',change_requests=[change('sets',4),change('reps','9'),change('rest_seconds',95)],resolved_query=message,confidence=.95,clarification_required=True,missing_slots=['周期'],clarification_question='周期几周？',task_update=TaskUpdate(action='new',task_id='',trigger=message,requirements=[{'key':'bench','quote':'卧推4组9次'},{'key':'rest','quote':'休息95秒'}]))
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
