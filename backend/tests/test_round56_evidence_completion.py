"""R55 actual evidence failures plus bounded read and scope counterexamples."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from app.config import settings
from app.services import agent_intent_model as model
from app.services.agent_intent import IntentResolution,required_read_tools,route_tools
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.services.agent_count_evidence import _count_scope,count_substitution
from app.services.agent_required_evidence import prepare_required_evidence
from app.services.agent_tools import NoArguments,WorkoutHistoryArguments,WorkoutProgressArguments
from app.schemas.agent_trace import AgentExecutionTrace
from app.services.business_clock import BUSINESS_DATE

ROWS=json.loads((Path(__file__).parent/'fixtures/round56/archived-paths.json').read_text(encoding='utf8'))['rows']
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9));monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-r56')
    monkeypatch.setattr(model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No network')))
    yield
    BUSINESS_DATE.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize('id',[r['id'] for r in ROWS])
async def test_baseline_archived_original_task_retains_core_obligations(id,monkeypatch):
    r=row(id);expected={'plan.get_active','health.get_screening_summary'} if id.startswith('WX') else {'plan.get_active','workout.list_history','workout.get_progress'}
    for raw in r['routes']:
        monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
        out=await resolve_intent_with_fallback(r['message'],use_model=True)
        assert not out.understanding_failed,out.error_category
        assert set(required_read_tools(out.resolution))==expected
        assert out.resolution.read_scope_quotes and all(q in r['message'] for q in out.resolution.read_scope_quotes)

@pytest.mark.parametrize('id',['CW02-r1','CW07-r1'])
def test_baseline_actual_complete_history_satisfies_count_despite_negative_intensity(id):
    r=row(id);trace=AgentExecutionTrace.model_validate(r['trace'])
    messages=[ToolMessage(content=t['tool_message']['content'],name=t['tool_message']['name'],tool_call_id=t['tool_message']['tool_call_id'],status='success') for t in r['tools']]
    proof=count_substitution(trace,{'messages':messages},r['message'])
    assert proof is not None
    assert proof.session_count==({'CW02-r1':2,'CW07-r1':3}[id])
    assert proof.statuses==(['completed','ended_early'] if id=='CW02-r1' else ['completed'])

def resolution(message,health=False,kind='query',risk='low'):
    return IntentResolution(primary_intent='plan_query' if health else 'workout_progress_query',intent_domain='workout_plan' if health else 'workout_progress',request_kind=kind,requested_effect='read',risk_level=risk,confidence=.99,resolved_query=message,evidence_requirements=['active_plan','health_screening'] if health else ['active_plan','workout_history','workout_progress'],read_scope_quotes=[message])

def tools(total=7,fail=None):
    calls=[];allow=['plan.get_active','health.get_screening_summary','workout.list_history','workout.get_progress'];items=[]
    for name in allow:
        async def invoke(_name=name,**args):
            calls.append((_name,args))
            if fail and fail[0]==_name:raise fail[1]
            if _name=='workout.list_history':
                n=min(total,args.get('limit',5));return {'range_start':args.get('start_date'),'range_end':args.get('end_date'),'as_of':'2026-10-09','timezone':'Asia/Shanghai','status_filter':args.get('statuses',['completed']), 'count':n,'total_count':total,'truncated':n<total,'sessions':[{'id':'s'+str(i),'trained_at':'2026-10-07','status':'completed'} for i in range(n)]}
            if _name=='workout.get_progress':return {'count_scope':{'start_date':args.get('start_date'),'end_date':args.get('end_date'),'as_of':'2026-10-09','timezone':'Asia/Shanghai','statuses':args.get('statuses')},'total_sessions':total,'aggregation':'all_matching_sessions'}
            return {'found':True,'injuries':[],'plan':{'weekly_completed_days':0}}
        schema=WorkoutHistoryArguments if name=='workout.list_history' else WorkoutProgressArguments if name=='workout.get_progress' else NoArguments
        items.append(StructuredTool.from_function(coroutine=invoke,name=name.replace('.','_'),description='Synthetic official read',args_schema=schema))
    return items,calls

@pytest.mark.asyncio
@pytest.mark.parametrize('total',[7,25])
async def test_baseline_bounded_count_read_completes_full_or_truncated_history(total):
    message=row('CW03-r1')['message'];r=resolution(message);ts,calls=tools(total)
    result=await prepare_required_evidence(None,tools=ts,resolution=r,allowlist=route_tools(r),query=message)
    assert result.active and not result.error_code and result.pending_tools==[]
    assert calls[0]==('plan.get_active',{})
    assert calls[1]==('workout.list_history',{'limit':20,'start_date':'2026-10-05','end_date':'2026-10-09','completed_only':False,'statuses':['completed']}),calls
    assert len(calls)==(2 if total==7 else 3)
    if total>20:assert calls[2]==('workout.get_progress',{'weeks':8,'start_date':'2026-10-05','end_date':'2026-10-09','statuses':['completed']})
    actual=[m for m in result.messages if isinstance(m,ToolMessage)]
    assert len(actual)==len(calls) and len({m.tool_call_id for m in actual})==len(calls)

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,risk',[('query','low'),('query','medium'),('assessment','medium')])
async def test_baseline_source_bound_temporary_plan_prefetches_health(kind,risk):
    message=row('WX11-r1')['message'];r=resolution(message,True,kind,risk);ts,calls=tools()
    result=await prepare_required_evidence(None,tools=ts,resolution=r,allowlist=route_tools(r),query=message)
    assert result.active and not result.error_code
    assert calls==[('plan.get_active',{}),('health.get_screening_summary',{})]

@pytest.mark.parametrize('suffix',['不推断强度。','不用据此判断强度。','无需评估训练强度。'])
def test_pure_negative_metric_is_not_required(suffix):
    assert _count_scope('核对本周已完成训练次数与计划完成日。'+suffix,date(2026,10,9))

@pytest.mark.parametrize('suffix',['判断强度。','不要不判断强度。','不判断强度但要分析容量。','别只统计次数，还要看每周平均。','计算计划完成率。','按动作列出组数。'])
def test_positive_other_metrics_are_not_replaced(suffix):
    assert _count_scope('核对本周已完成训练次数与计划完成日。'+suffix,date(2026,10,9)) is None

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['authority','budget','wrong_quote','write','high'])
async def test_completion_cannot_expand_authority_or_budget(change,monkeypatch):
    msg=row('CW03-r1')['message'];r=resolution(msg);ts,calls=tools();allow=route_tools(r)
    if change=='authority':allow.remove('plan.get_active')
    elif change=='budget':monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',2)
    elif change=='wrong_quote':r.read_scope_quotes=['not in message']
    elif change=='write':r.requested_effect='update'
    else:r.risk_level='high'
    result=await prepare_required_evidence(None,tools=ts,resolution=r,allowlist=allow,query=msg)
    assert not calls
    assert result.error_code or not result.active

@pytest.mark.asyncio
async def test_failed_aggregate_keeps_truncated_read_and_all_attempts():
    msg=row('CW03-r1')['message'];r=resolution(msg);ts,calls=tools(25,('workout.get_progress',TimeoutError()))
    result=await prepare_required_evidence(None,tools=ts,resolution=r,allowlist=route_tools(r),query=msg)
    assert result.error_code=='read_tool_timeout' and len(calls)==4
    messages=[m for m in result.failure_result()['messages'] if isinstance(m,ToolMessage)]
    assert json.loads(messages[1].content)['truncated'] is True
    assert [m.status for m in messages]==['success','success','error','error']

@pytest.mark.parametrize('args',[{'start_date':'2026-10-05'},{'statuses':['completed']},{'start_date':'2026-10-10','end_date':'2026-10-05'},{'start_date':'2026-10-05','end_date':'2026-10-09','statuses':['invented']},{'user_id':'other'}])
def test_exact_progress_scope_rejects_invalid_or_foreign_args(args):
    with pytest.raises(ValueError):WorkoutProgressArguments(**args)


def test_history_substitution_does_not_satisfy_other_missing_obligation():
    from app.services.agent_evidence_contract import check_evidence
    r=row('CW07-r1');trace=AgentExecutionTrace.model_validate(r['trace'])
    trace.evidence_contract.required_tools.append('health.get_screening_summary')
    messages=[ToolMessage(content=t['tool_message']['content'],name=t['tool_message']['name'],tool_call_id=t['tool_message']['tool_call_id'],status='success') for t in r['tools']]
    checked=check_evidence(trace,result={'messages':messages},query=r['message'])
    assert checked.evidence_contract.missing_tools==['health.get_screening_summary']
    assert checked.evidence_contract.status=='partial' and len(checked.evidence_contract.substitutions)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad',[None, [], {}, {'aggregation':'all_matching_sessions','total_sessions':25,'count_scope':None}])
async def test_malformed_aggregate_fails_with_retained_history(bad):
    msg=row('CW03-r1')['message'];r=resolution(msg);ts,calls=tools(25)
    async def malformed(**kwargs):return bad
    next(t for t in ts if t.name=='workout_get_progress').coroutine=malformed
    result=await prepare_required_evidence(None,tools=ts,resolution=r,allowlist=route_tools(r),query=msg)
    assert result.error_code=='read_tool_argument_invalid'
    messages=[m for m in result.failure_result()['messages'] if isinstance(m,ToolMessage)]
    assert len(messages)==3 and json.loads(messages[1].content)['truncated'] is True
