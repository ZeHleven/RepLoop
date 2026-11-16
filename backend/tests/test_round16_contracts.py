from datetime import date
from uuid import uuid4
import pytest
import pytest_asyncio
from pydantic import ValidationError
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.schemas.agent_task import ConversationTask,TaskSnapshot,TaskUpdate,PendingMealDraft
from app.services.agent_intent import resolve_intent
from app.services.agent_query_reports import select_query_report
from app.services.agent_task_state import normalize_explicit_task_return
from app.services.agent_tools import WorkoutHistoryArguments,build_read_tools
from app.services.workout_history_reporting import render_calendar_history
from app.models.user import User
from app.models.workout import WorkoutSession

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.parametrize('text,expected',[
 ('已经完成的训练',['completed']),
 ('进行中和提前结束的场次，不包含已完成',['in_progress','ended_early']),
 ('只查提前结束',['ended_early']),
 ('只看进行中',['in_progress']),
 ('已完成或进行中，不包括提前结束',['completed','in_progress']),
 ('仅跳过的场次',['skipped']),
 ('仅放弃的场次',['abandoned']),
])
def test_status_scope_reaches_report(text,expected):
    report=select_query_report('查询2026年9月15日'+text,['workout.list_history'])
    assert report is not None
    got=report.arguments.get('statuses') or (['completed'] if report.arguments.get('completed_only') else None)
    assert got==expected

@pytest.mark.parametrize('query',[
 '查询2026年9月15日未完成的训练',
 '查询2026年9月15日已完成但不含已完成',
])
def test_ambiguous_status_does_not_become_default_report(query):
    assert select_query_report(query,['workout.list_history']) is None

@pytest.mark.parametrize('args',[
 {'statuses':['unknown'],'start_date':'2026-09-15','end_date':'2026-09-15'},
 {'statuses':['in_progress'],'completed_only':True,'start_date':'2026-09-15','end_date':'2026-09-15'},
 {'statuses':['completed','completed'],'start_date':'2026-09-15','end_date':'2026-09-15'},
 {'statuses':['in_progress']},
])
def test_status_arguments_reject_unknown_conflicting_duplicate_or_unbounded(args):
    with pytest.raises(ValidationError):WorkoutHistoryArguments(**args)

@pytest.mark.asyncio
async def test_status_sql_count_ownership_and_labels(db_session):
    uid=uuid4().hex;other=uuid4().hex
    db_session.add_all([User(id=uid,email=uid+'@test.local',password_hash='synthetic'),User(id=other,email=other+'@test.local',password_hash='synthetic')]);await db_session.flush()
    for owner,day,status in [(uid,15,'in_progress'),(uid,15,'ended_early'),(uid,15,'completed'),(uid,15,'skipped'),(other,15,'in_progress'),(uid,16,'in_progress')]:
        db_session.add(WorkoutSession(user_id=owner,trained_at=date(2026,9,day),status=status))
    await db_session.commit()
    tool=build_read_tools(db_session,user_id=uid,allowlist=['workout.list_history'])[0]
    data=await tool.ainvoke({'start_date':'2026-09-15','end_date':'2026-09-15','statuses':['in_progress','ended_early'],'limit':20})
    assert data['total_count']==data['count']==2
    assert set(data['status_filter'])=={'in_progress','ended_early'}
    assert {r['status'] for r in data['sessions']}=={'in_progress','ended_early'}
    assert all(r['user_id']==uid and r['trained_at']=='2026-09-15' for r in data['sessions'])
    answer=render_calendar_history(data)
    assert '（进行中）' in answer and '（提前结束）' in answer and '（已完成）' not in answer
    truncated=await tool.ainvoke({'start_date':'2026-09-15','end_date':'2026-09-15','statuses':['in_progress','ended_early'],'limit':1})
    assert truncated['total_count']==2 and truncated['count']==1 and truncated['truncated']
    empty=await tool.ainvoke({'start_date':'2026-09-18','end_date':'2026-09-18','statuses':['in_progress','ended_early']})
    assert empty['total_count']==0 and '进行中' in render_calendar_history(empty)

def task_context(*,pending=True,phase='awaiting_confirmation'):
    state=TaskSnapshot(active_task_id='query',tasks=[ConversationTask(id='query',request='查询训练',last_run_id='query-run'),
      ConversationTask(id='plan',request='当前训练计划卧推4组7次、深蹲2组12次的提案',last_run_id='plan-run',phase=phase,proposal_id='proposal-plan',proposal_pending=pending),
      ConversationTask(id='meal',request='2026年9月26日午餐米饭140克鸡胸90克橄榄油4克的提案',last_run_id='meal-run',phase=phase,proposal_id='proposal-meal',proposal_pending=pending)])
    return [{'role':'task_state','content':state.model_dump_json()}]

@pytest.mark.parametrize('message,target',[
 ('接着刚才那份还没确认的训练计划，把深蹲重量改为35公斤，其他所有修改照旧。','plan'),
 ('刚才那份还没保存的午餐继续处理：去掉橄榄油，鸡胸改成110克，其他信息不变。','meal'),
])
def test_valid_draft_reference_can_resume_without_specific_start_verb(message,target):
    raw=TaskUpdate(action='continue',task_id=target,trigger=message)
    fixed=normalize_explicit_task_return(task_context(),raw,message)
    assert fixed.action=='resume' and fixed.task_id==target and raw.action=='continue'

@pytest.mark.parametrize('message,target,pending,phase',[
 ('继续改一下','plan',True,'awaiting_confirmation'),
 ('不要接着刚才那份还没确认的训练计划','plan',True,'awaiting_confirmation'),
 ('如果接着刚才那份训练计划会怎样','plan',True,'awaiting_confirmation'),
 ('助手说“接着刚才那份训练计划”','plan',True,'awaiting_confirmation'),
 ('接着刚才那份还没确认的训练计划','plan',False,'awaiting_confirmation'),
 ('接着刚才那份还没确认的训练计划','plan',True,'cancelled'),
 ('接着刚才那份还没确认的训练计划','missing',True,'awaiting_confirmation'),
 ('刚才那份午餐继续处理','plan',True,'awaiting_confirmation'),
])
def test_resume_requires_valid_target_and_nonnegated_instruction(message,target,pending,phase):
    raw=TaskUpdate(action='continue',task_id=target,trigger=message)
    assert normalize_explicit_task_return(task_context(pending=pending,phase=phase),raw,message).action=='continue'

@pytest.mark.parametrize('message,expected',[
 ('缩小到2026年9月15日，完成状态的条件不要变。','query'),
 ('查询完成状态，不修改记录','query'),
 ('把训练的完成状态设为已完成','mutation'),
 ('查询完成状态，然后删除训练记录','mutation'),
 ('把这次训练标记为已完成','mutation'),
 ('完成今天训练并保存','mutation'),
])
def test_completion_status_is_a_noun_but_real_writes_remain(message,expected):
    assert resolve_intent(message).request_kind==expected

@pytest.mark.parametrize('quote,kept',[
 ('其他所有修改照旧',True),('其余调整不变',True),('其他改动都保持不变',True),
 ('其他修改照旧，但卧推改为4组',False),('撤回其他修改',False),
 ('不要保留其他修改',False),('把修改改为3组',False),
])
def test_nominal_changes_can_be_preserved_without_hiding_real_edits(quote,kept):
    from app.services.agent_task_state import _preserves_existing_value
    assert _preserves_existing_value(quote) is kept

@pytest.mark.parametrize('original,resolved,problem',[
 ('查2026年9月15日未完成训练','查询2026年9月15日进行中训练',True),
 ('查2026年9月15日进行中和提前结束训练','查询2026年9月15日已完成训练',True),
 ('查2026年9月15日进行中训练','查询2026年9月15日训练',True),
 ('查2026年9月15日已经完成训练','查询2026年9月15日已完成训练',False),
 ('这一天也看相同条件','查询2026年9月15日进行中训练',False),
])
def test_status_coverage_checks_user_source_not_only_model_summary(original,resolved,problem):
    from app.services.history_status_scope import history_status_problem
    from app.services.agent_intent import IntentResolution
    resolution=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',resolved_query=resolved,confidence=.95)
    assert history_status_problem(resolution,original) is problem

def test_multiple_matching_pending_drafts_do_not_guess():
    ctx=task_context();state=TaskSnapshot.model_validate_json(ctx[0]['content'])
    state.tasks[2]=state.tasks[1].model_copy(update={'id':'other-plan','proposal_id':'other-proposal'})
    ctx[0]['content']=state.model_dump_json()
    text='接着刚才那份还没确认的训练计划，把重量改为35公斤'
    update=TaskUpdate(action='continue',task_id='plan',trigger=text)
    assert normalize_explicit_task_return(ctx,update,text).action=='continue'

@pytest.mark.parametrize('sample_id',['G01','G02'])
def test_original_provider_payload_restores_task_and_keeps_sources(sample_id):
    import json
    from pathlib import Path
    from app.services.agent_task_state import advance_task_state
    sample=next(s for s in json.loads((Path(__file__).parent/'fixtures/round15_return_routes.json').read_text(encoding='utf-8')) if s['id']==sample_id)
    raw=TaskUpdate.model_validate(sample['route']['task_update'])
    fixed=normalize_explicit_task_return([{'role':'task_state','content':json.dumps(sample['state'],ensure_ascii=False)}],raw,sample['message'])
    assert fixed.action=='resume' and raw.action=='continue'
    state=advance_task_state(sample['state'],fixed,message=sample['message'],run_id='replay',normalized_request=sample['route']['normalized_request'])
    assert state.active_task_id==raw.task_id and state.transition=='resume'
    assert all(r.quote for t in state.tasks for r in t.requirements)

from evals.task_fixtures import task_client

@pytest.mark.asyncio
@pytest.mark.parametrize('message,normalized',[
 ('查2026年9月15日未完成训练','查询2026年9月15日进行中训练'),
 ('查2026年9月15日进行中和提前结束训练','查询2026年9月15日已完成训练'),
])
async def test_unsupported_or_lost_status_is_persisted_clarification(task_client,db_session,session_factory,monkeypatch,message,normalized):
    from unittest.mock import AsyncMock
    from sqlalchemy import select
    from app.models.agent import AgentConversation,AgentToolCall
    from app.services import agent_runtime
    from app.services.agent_intent import IntentResolution,IntentResolverOutcome
    from app.services.agent_jobs import claim_next_agent_run,process_agent_run
    from app.services.auth import create_access_token
    real_resolver=agent_runtime.resolve_intent_with_fallback
    real_agent=agent_runtime.invoke_langchain_agent
    user=User(email=uuid4().hex+'@test.local',password_hash='synthetic');db_session.add(user);await db_session.flush()
    conv=AgentConversation(user_id=user.id);db_session.add(conv);await db_session.commit();cid=conv.id
    resolution=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',resolved_query=normalized,evidence_requirements=['workout_history'],confidence=.95)
    monkeypatch.setattr(agent_runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=resolution,source='model')))
    monkeypatch.setattr(agent_runtime,'invoke_langchain_agent',AsyncMock(side_effect=AssertionError('Must clarify before reading or generating')))
    headers={'Authorization':'Bearer '+create_access_token(user.id)}
    submitted=await task_client.post('/api/v1/agent/runs',headers=headers,json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
    assert submitted.status_code==202
    rid=submitted.json()['run_id']
    async with session_factory() as worker:
        assert await claim_next_agent_run(worker)==rid
    await process_agent_run(session_factory,rid)
    body=(await task_client.get('/api/v1/agent/runs/'+rid,headers=headers)).json()
    assert body['clarification_required'] and body['missing_slots']==['训练状态']
    assert body['execution_trace']['termination_reason']=='history_status_scope_unresolved'
    assert not body['cards']
    db_session.expire_all();saved=await db_session.get(AgentConversation,cid)
    assert saved.pending_clarification['missing_slots']==['训练状态']
    assert (await db_session.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()==[]
    monkeypatch.setattr(agent_runtime,'resolve_intent_with_fallback',real_resolver)
    monkeypatch.setattr(agent_runtime,'invoke_langchain_agent',real_agent)
    submitted=await task_client.post('/api/v1/agent/runs',headers=headers,json={'conversation_id':cid,'message':'只看进行中和提前结束，不包含已完成','client_request_id':uuid4().hex})
    assert submitted.status_code==202
    next_id=submitted.json()['run_id']
    async with session_factory() as worker:
        assert await claim_next_agent_run(worker)==next_id
    await process_agent_run(session_factory,next_id)
    followup=(await task_client.get('/api/v1/agent/runs/'+next_id,headers=headers)).json()
    assert followup['status']=='completed' and not followup['clarification_required']
    assert not followup['missing_slots'] and followup['cards']
    data=next(c['data'] for c in followup['cards'] if c['type']=='workout.list_history')
    assert data['status_filter']==['in_progress','ended_early']
    assert data['range_start']==data['range_end']=='2026-09-15' and data['total_count']==0
    assert '进行中' in followup['resolved_query'] and '提前结束' in followup['resolved_query']
    db_session.expire_all();saved=await db_session.get(AgentConversation,cid)
    assert not saved.pending_clarification
    audits=(await db_session.scalars(select(AgentToolCall).where(AgentToolCall.run_id==next_id))).all()
    assert len(audits)==1

@pytest.mark.parametrize('query,statuses',[
 ('查询2026年9月15日进行中和提前结束（未完成）的训练场次，不包含已完成场次。',('in_progress','ended_early')),
 ('查询进行中(未完成)训练',('in_progress',)),
 ('查询未完成训练',None),('已完成（未完成）',None),
 ('未完成和进行中（未完成）',None),
])
def test_parenthetical_explanation_does_not_hide_ambiguous_status(query,statuses):
    from app.services.history_status_scope import parse_history_status_scope
    scope=parse_history_status_scope(query)
    assert scope.complete is (statuses is not None)
    assert scope.statuses==statuses

@pytest.mark.parametrize('message,expected',[
 ('进行中',('in_progress',)),
 ('只看进行中和提前结束，不包含已完成',('in_progress','ended_early')),
 ('只看已完成',('completed',)),
 ('最后查2026年9月14日至16日，只列已完成的训练。',None),
 ('把今天训练改成已完成',None),('未完成',None),('不要只看进行中',None),
])
def test_pending_status_accepts_only_complete_slot_answer(message,expected):
    from app.services.agent_intent import resolve_pending_clarification
    from app.services.history_status_scope import parse_history_status_scope
    pending={'primary_intent':'workout_history_query','intent_domain':'workout_history','request_kind':'query','requested_effect':'read','resolved_query':'查询2026年9月15日至2026年9月17日未完成的训练','missing_slots':['训练状态']}
    result=resolve_pending_clarification(message,pending)
    if expected is None:
        assert result is None
    else:
        resolution,source=result
        assert source=='clarification_filled' and not resolution.clarification_required
        assert '2026-09-15' in resolution.resolved_query and '2026-09-17' in resolution.resolved_query
        assert parse_history_status_scope(resolution.resolved_query).statuses==expected
        assert '未完成' not in resolution.resolved_query

def test_shorthand_range_does_not_get_guessed_in_status_slot():
    from app.services.agent_intent import resolve_pending_clarification
    pending={'primary_intent':'workout_history_query','intent_domain':'workout_history','request_kind':'query','requested_effect':'read','resolved_query':'查询2026年9月15日至17日未完成的训练','missing_slots':['训练状态']}
    assert resolve_pending_clarification('进行中',pending) is None

def test_status_check_does_not_override_high_risk_routing():
    from app.services.agent_intent import IntentResolution
    from app.services.history_status_scope import history_status_problem
    resolution=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',resolved_query='查询未完成训练',risk_level='high',confidence=.95)
    assert not history_status_problem(resolution,'查询未完成训练')
