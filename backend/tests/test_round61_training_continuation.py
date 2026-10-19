"""Original R60 failures and invariant probes; no external model calls."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.config import settings
from app.services import agent_runtime as runtime,agent_intent_model as routing
from app.services.agent_intent import IntentResolution,route_tools
from app.services.business_clock import BUSINESS_DATE
from test_round57_read_delivery import install_tools,scripted_http

ROWS=json.loads((Path(__file__).parent/'fixtures/round61/archived-paths.json').read_text(encoding='utf8'))['rows']
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))
def resolution(body):
    return IntentResolution.model_validate({**{k:v for k,v in body.items() if k in IntentResolution.model_fields},'confidence':body['intent_confidence']})
def task_state(r):
    from app.schemas.agent_task import TaskSnapshot
    return TaskSnapshot.model_validate(r['turns'][1]['body']['execution_trace']['task_state'])

@pytest.mark.asyncio
async def test_archived_minimum_training_advice_requires_health_and_plan(monkeypatch):
    from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
    from app.services.agent_intent import required_read_tools
    r=row('WX21-r1')
    monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(r['routes'][0])))
    out=await resolve_intent_with_fallback(r['turns'][0]['message'],use_model=True)
    assert not out.understanding_failed
    assert set(required_read_tools(out.resolution))=={'plan.get_active','health.get_screening_summary'}
    assert out.resolution.read_scope_quotes

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline-r61')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(routing,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external call')))
    yield
    BUSINESS_DATE.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['WX35-r1','WX35-r2'])
async def test_baseline_followup_required_reads_are_fresh_and_auditable(id,monkeypatch):
    r=row(id);turn=r['turns'][1];res=resolution(turn['body']);assert res.read_scope_quotes==[]
    calls=install_tools(monkeypatch,r)
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':'离线执行检查，不计真实业务成功。'}])
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[{'role':'assistant','content':r['turns'][0]['body']['reply']}],user_message=turn['message'],tool_allowlist=route_tools(res),resolution=res,resolved_query=res.resolved_query,task_state=task_state(r))
    assert {c[0] for c in calls}=={'plan.get_active','health.get_screening_summary'}
    assert len(runtime._tool_call_records(result,'r61'))==2
    assert sum(m['role']=='tool' for m in requests[0]['messages'])==2
    assert not requests[0].get('tools'),'Satisfied obligations must not be re-read'

@pytest.mark.parametrize('id,cap',[('WX21-r1',6),('WX23-r1',5),('WX28-r2',12)])
def test_baseline_short_plans_have_checked_weekly_and_time_envelope(id,cap):
    from app.services.assessment_grounding import project_evidence
    r=row(id);data=next(t['result'] for t in r['tools'] if t['tool']=='plan.get_active')
    projected=project_evidence('plan.get_active',data)
    assert 'training_schedule' in projected['boundaries'],'Free-form delivery needs the same computable plan baseline'
    from app.services.agent_training_constraints import adjustment_budget
    spec=adjustment_budget(projected['boundaries']['training_schedule'],cap)
    assert spec['temporary_weekly_sets']<spec['baseline_weekly_sets']
    assert spec['temporary_days']<=spec['baseline_days']
    assert all(s['estimated_seconds']<=cap*60 for s in spec['sessions'])
    assert spec['count_rest_after_each_set'] is True

@pytest.mark.asyncio
async def test_baseline_generation_receives_time_and_plan_limits(monkeypatch):
    r=row('WX28-r2');turn=r['turns'][0];res=resolution(turn['body'])
    # Restore the exact source anchor retained in the archived trace.
    res.read_scope_quotes=turn['body']['execution_trace']['evidence_contract']['scope_quotes']
    install_tools(monkeypatch,r);requests=scripted_http(monkeypatch,[{'role':'assistant','content':'离线约束检查，不计业务完成。'}])
    await runtime.invoke_langchain_agent(None,user_id='s',history=[],user_message=turn['message'],tool_allowlist=route_tools(res),resolution=res)
    system='\n'.join(m['content'] for m in requests[0]['messages'] if m['role']=='system')
    assert 'training_adjustment_envelope' in system
    assert '"session_cap_minutes": 12' in system
    assert '不能' in system and '周' in system

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['high','write','clarify','forbidden','budget'])
async def test_prefetch_keeps_authority_and_budget_gates(monkeypatch,change):
    from app.services.agent_required_evidence import prepare_required_evidence
    r=row('WX35-r1');res=resolution(r['turns'][1]['body']);allowed=route_tools(res)
    if change=='high':res.risk_level='high'
    elif change=='write':res.request_kind='mutation';res.requested_effect='update'
    elif change=='clarify':res.clarification_required=True
    elif change=='forbidden':allowed=['health.get_screening_summary']
    else:monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',1)
    calls=install_tools(monkeypatch,r);tools=runtime.build_read_tools(None,user_id='s',allowlist=allowed)
    result=await prepare_required_evidence(None,tools=tools,resolution=res,allowlist=allowed,query=r['turns'][1]['message'],task_state=task_state(r))
    assert not calls
    if change in {'forbidden','budget'}:assert result.error_code==('read_tool_permission_denied' if change=='forbidden' else 'read_tool_budget_exhausted')
    else:assert not result.active

@pytest.mark.asyncio
async def test_unanchored_parameterized_requirement_is_not_guessed(monkeypatch):
    from app.services.agent_required_evidence import prepare_required_evidence
    r=row('WX35-r1');res=resolution(r['turns'][1]['body'])
    res.evidence_requirements=['active_plan','workout_progress'];res.intent_domain='workout_progress';allowed=route_tools(res)
    from app.schemas.agent_task import TaskRequirement
    state=task_state(r);state.tasks[0].requirements=[TaskRequirement(key='count',quote='核对本周计划完成日和已完成训练场次',source_run_id='synthetic-current-task')]
    calls=install_tools(monkeypatch,r);tools=runtime.build_read_tools(None,user_id='s',allowlist=allowed)
    result=await prepare_required_evidence(None,tools=tools,resolution=res,allowlist=allowed,query='根据刚才的补充继续。',task_state=state)
    assert calls==[('plan.get_active',{})]
    assert result.pending_tools==['workout.get_progress']

@pytest.mark.parametrize('days,sets,rest,cap',[(1,1,180,5),(1,1,90,15),(1,2,180,6),(2,5,300,10),(3,4,90,12),(7,10,300,60),(1,3,90,1)])
def test_workload_and_time_invariants_including_zero_work_sets(days,sets,rest,cap):
    from app.services.agent_training_constraints import training_schedule,adjustment_budget
    data={'found':True,'plan':{'is_active':True,'days_per_week':days,'exercises':[{'day_of_week':d,'sets':sets,'reps':'8-12','rest_seconds':rest} for d in range(1,days+1)]}}
    spec=adjustment_budget(training_schedule(data),cap)
    assert 0<=spec['temporary_weekly_sets']<days*sets
    assert spec['temporary_days']<=min(days,2)
    assert sum(e['sets'] for s in spec['sessions'] for e in s['exercises'])==spec['temporary_weekly_sets']
    assert all(s['estimated_seconds']==240+sum(e['sets']*(e['reps']*4+e['rest_seconds']) for e in s['exercises'])<=60*cap for s in spec['sessions'])
    assert all(e['rest_seconds']==rest for s in spec['sessions'] for e in s['exercises'])
    if spec['temporary_weekly_sets']==0:assert spec['allocation_status']=='no_work_sets_fit' and spec['temporary_days']==0

@pytest.mark.parametrize('change',['days','boolean_sets','rest','reps','inactive','empty','ambiguous'])
def test_unknown_or_conflicting_schedule_does_not_invent_numbers(change):
    from app.services.agent_training_constraints import training_schedule
    data=next(t['result'] for t in row('WX28-r2')['tools'] if t['tool']=='plan.get_active');p=data['plan']
    if change=='days':p['days_per_week']=7
    elif change=='boolean_sets':p['exercises'][0]['sets']=True
    elif change=='rest':p['exercises'][0]['rest_seconds']=None
    elif change=='reps':p['exercises'][0]['reps']='AMRAP'
    elif change=='inactive':p['is_active']=False
    elif change=='empty':p['exercises']=[]
    else:p['exercises'][0]['reps']='12-8'
    assert training_schedule(data) is None

@pytest.mark.parametrize('query',['每次15分钟也可能10分钟，给临时建议。','解释“临时训练每次15分钟”这句话。','保存为新计划，每次15分钟。'])
def test_ambiguous_quoted_or_write_request_is_not_a_numeric_prescription(query):
    from app.services.agent_training_constraints import training_adjustment_context
    from langchain_core.messages import ToolMessage
    r=row('WX28-r2');res=resolution(r['turns'][0]['body'])
    if '保存为' in query:res.request_kind='mutation';res.requested_effect='create'
    messages=[ToolMessage(content=json.dumps(t['result']),name=t['tool'].replace('.','_'),tool_call_id=str(i)) for i,t in enumerate(r['tools'])]
    assert training_adjustment_context(query,res,messages)==''

@pytest.mark.asyncio
async def test_protocol_repair_retains_numeric_limits(monkeypatch):
    r=row('WX28-r2');t=r['turns'][0];res=resolution(t['body']);install_tools(monkeypatch,r)
    res.read_scope_quotes=t['body']['execution_trace']['evidence_contract']['scope_quotes']
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':'<tool_call>bad</tool_call>'},{'role':'assistant','content':'离线修复检查'}])
    await runtime.invoke_langchain_agent(None,user_id='s',history=[],user_message=t['message'],tool_allowlist=route_tools(res),resolution=res)
    assert len(requests)==2
    assert any('training_adjustment_envelope' in m['content'] for m in requests[-1]['messages'] if m['role']=='system')
    assert not requests[-1].get('tools')

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['none','new','cancelled','different_task','unbacked','wrong_quote'])
async def test_only_validated_active_continuation_can_inherit_reads(monkeypatch,change):
    from app.services.agent_required_evidence import prepare_required_evidence
    r=row('WX35-r1');res=resolution(r['turns'][1]['body']);state=task_state(r)
    if change=='none':state=None
    elif change=='new':state.transition='new'
    elif change=='cancelled':state.tasks[0].phase='cancelled'
    elif change=='different_task':state.active_task_id='another-task'
    elif change=='unbacked':state.tasks[0].requirements=[]
    else:res.read_scope_quotes=['invented quotation not in user message']
    calls=install_tools(monkeypatch,r);allow=route_tools(res);tools=runtime.build_read_tools(None,user_id='s',allowlist=allow)
    prepared=await prepare_required_evidence(None,tools=tools,resolution=res,allowlist=allow,query=r['turns'][1]['message'],task_state=state)
    assert not calls and not prepared.active

@pytest.mark.parametrize('query,expected',[('根据我刚才补充继续。',12),('现在每次改为8分钟，只给建议。',8),('每次也许8分钟也许12分钟。',None)])
def test_continued_time_cap_uses_validated_quotes_with_current_override(query,expected):
    from langchain_core.messages import ToolMessage
    from app.services.agent_training_constraints import training_adjustment_context
    r=row('WX35-r1');state=task_state(r);res=resolution(r['turns'][1]['body'])
    messages=[ToolMessage(content=json.dumps(t['result']),name=t['tool'].replace('.','_'),tool_call_id=str(i)) for i,t in enumerate(r['tools'])]
    context=training_adjustment_context(query,res,messages,state)
    if expected is None:assert not context
    else:
        value=json.loads(context.splitlines()[-1]);assert value['training_adjustment_envelope']['session_cap_minutes']==expected
        assert value['limit_source']==('validated_active_task_requirements' if '刚才' in query else 'current_user_message')
