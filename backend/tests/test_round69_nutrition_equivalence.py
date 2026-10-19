"""Actual old failures, exact observation provenance, and adversarial scope pairs."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import ToolMessage
from app.schemas.agent_trace import AgentExecutionTrace
from app.services.agent_trace import observation_fingerprint
from app.services.agent_evidence_contract import check_evidence,guard_read_completion
from app.services.agent_nutrition_scope import requested_nutrition_dates,nutrition_read_evidence
from app.services.agent_nutrition_evidence import nutrition_substitutions
from app.services.business_clock import BUSINESS_DATE
from app.services.agent_intent import required_read_tools
from app.config import settings
from app.services import agent_intent_model as model
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback

ROWS=json.loads((Path(__file__).parent/'fixtures/round69/archived.json').read_text(encoding='utf8'))['rows']

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline-r69')
    monkeypatch.setattr(model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No network')))
    yield
    BUSINESS_DATE.reset(token)

def archived(id='NX01-r1'):
    r=json.loads(next(x['run_json'] for x in ROWS if x['id']==id))
    trace=AgentExecutionTrace.model_validate(r['turns'][0]['body']['execution_trace'])
    messages=[]
    for action in trace.actions:
        if action.status!='completed':continue
        t=next(x for x in r['tool_attempts'] if x['tool']==action.tool_id and x['status']=='success')
        content=json.dumps(t['result'],ensure_ascii=False)
        assert observation_fingerprint(content)==next(o.result_fingerprint for o in trace.observations if o.call_id==action.call_id)
        messages.append(ToolMessage(content=content,name=action.tool_id.replace('.','_'),tool_call_id=action.call_id))
    return r,trace,{'messages':messages},r['turns'][0]['message']

@pytest.mark.parametrize('id',[x['id'] for x in ROWS])
def test_archived_eight_contracts_use_actual_scoped_records(id):
    r,trace,result,query=archived(id)
    if id=='NX05-r1':
        evidence,optional,quotes=nutrition_read_evidence(query,domain='nutrition',kind='assessment',effect='read',risk='low',evidence=['nutrition_history','nutrition_today'])
        assert optional==['nutrition_today'] and quotes[0] in query
        trace.evidence_contract.required_tools=['nutrition.list_history']
        trace.evidence_contract.optional_tools=['nutrition.get_today']
    updated=check_evidence(trace,result=result,query=query)
    assert updated.evidence_contract.status=='complete'
    assert bool(updated.evidence_contract.substitutions)==(id!='NX05-r1')
    assert check_evidence(AgentExecutionTrace.model_validate(updated.model_dump(mode='json'))).evidence_contract.status=='complete'
    reply,after=guard_read_completion('Only a pipeline assertion, not a graded business answer.',updated,result=result,query=query)
    assert reply.startswith('Only a pipeline assertion')
    for proof in after.evidence_contract.substitutions:
        assert proof.actual_intake_completeness=='unknown'
        assert proof.call_id in [o.call_id for o in trace.observations]

@pytest.mark.asyncio
@pytest.mark.parametrize('id',[x['id'] for x in ROWS])
async def test_original_model_route_retains_profile_and_refines_only_nutrition_dates(id,monkeypatch):
    r,_,_,query=archived(id)
    routes=[json.loads(c['function']['arguments']) for req in r['provider'] for choice in req.get('response',{}).get('choices',[]) for c in choice['message'].get('tool_calls',[]) if c['function']['name']=='submit_semantic_route']
    assert routes
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(routes[0])))
    outcome=await resolve_intent_with_fallback(query,use_model=True)
    assert not outcome.understanding_failed,outcome.error_category
    required=set(required_read_tools(outcome.resolution))
    assert required & {'nutrition.list_history','nutrition.get_today'} == ({'nutrition.get_today'} if id=='NA15-r1' else {'nutrition.list_history'})
    if id!='NX05-r1':assert 'profile.get_summary' in required
    assert 'nutrition.get_recent_context' not in required
    assert all(q in query for q in outcome.resolution.read_scope_quotes)

@pytest.mark.parametrize('query',[
    '不要评估昨天午餐，评估上周。','“昨天午餐”只是举例，请看最近一个月。',
    '评估昨天午餐，和上周对比。','评估昨天午餐的前一天记录。',
    '评估昨天午餐，然后改成今天晚餐。','评估昨天午餐和明天计划。',
    '昨天的其他日期记录也核对。','评估昨天午餐及近期趋势。',
    '评估2026-10-10午餐。','评估昨天午餐之前的记录。','昨天训练完成了吗？',
    '比较昨天和前天饮食。','评估昨天午餐，近三天的平均也计算。',
    '评估昨天午餐，也看这几天的整体记录。','评估昨天午餐，上次的也看。',
])
def test_unsupported_compound_dates_never_drop_obligations(query):
    original=['nutrition_history','nutrition_today','profile_summary','nutrition_recent_context']
    assert requested_nutrition_dates(query,date(2026,10,9)) is None
    actual,optional,quotes=nutrition_read_evidence(query,domain='nutrition',kind='assessment',effect='read',risk='low',evidence=original)
    assert (actual,optional,quotes)==(original,[],[])

@pytest.mark.parametrize('query,dates',[
    ('评估昨天午餐，给一条建议。',['2026-10-08']),
    ('前天三餐记了，请核对营养。',['2026-10-07']),
    ('请评估2026-10-08晚餐的营养。',['2026-10-08']),
    ('查询2026-10-07至2026-10-08每日蛋白质。',['2026-10-07','2026-10-08']),
    ('今天只记了午餐，晚餐还没吃，请评价记录。',['2026-10-09']),
])
def test_source_operands_are_not_model_invented(query,dates):
    result=requested_nutrition_dates(query,date(2026,10,9))
    assert result and list(result[0])==dates and all(q in query for q in result[1])

@pytest.mark.parametrize('mode',['hash','call_id','action_failed','observation_failed','stale_clock','wrong_day','missing_items','wrong_total','duplicate_id','meal_date','meal_filter','scope_count','scope_timezone','wrong_limit','empty_past','actual_intake_claim','read_error','safe_stop','profile_missing'])
def test_invalid_data_or_unrelated_missing_evidence_cannot_satisfy_contract(mode):
    r,t,result,q=archived();m=result['messages'][0];data=json.loads(m.content)
    if mode=='hash':m.content=m.content+' '
    elif mode=='call_id':m.tool_call_id='other-run'
    elif mode=='action_failed':t.actions[0].status='failed'
    elif mode=='observation_failed':t.observations[0].status='error'
    elif mode=='stale_clock':t.request_context.as_of='2026-10-08'
    elif mode=='read_error':result['read_error_code']='timeout'
    elif mode=='safe_stop':t.terminal_action='safe_stop'
    elif mode=='profile_missing':
        t.observations=[o for o in t.observations if o.tool_id!='profile.get_summary']
    else:
        if mode=='wrong_day':data['recent_days'][0]['date']='2026-10-07'
        elif mode=='missing_items':del data['recent_days'][0]['meals'][0]['items']
        elif mode=='wrong_total':data['recent_days'][0]['total_protein_g']+=1
        elif mode=='duplicate_id':data['recent_days'][0]['meals'].append(copy.deepcopy(data['recent_days'][0]['meals'][0]))
        elif mode=='meal_date':data['recent_days'][0]['meals'][0]['logged_at']='2026-10-07'
        elif mode=='meal_filter':data['scope']['meal_types']=['午餐']
        elif mode=='scope_count':data['scope']['returned_dates']=3
        elif mode=='scope_timezone':data['scope']['timezone']='UTC'
        elif mode=='wrong_limit':data['scope']['requested_limit']=30
        elif mode=='empty_past':data['recent_days']=[];data['scope'].update(returned_dates=0,first_logged_date=None,last_logged_date=None)
        elif mode=='actual_intake_claim':data['evidence_limits']['whole_day_completeness']='complete'
        m.content=json.dumps(data,ensure_ascii=False)
        t.observations[0].result_fingerprint=observation_fingerprint(m.content)
    if mode=='hash':t.observations[0].result_fingerprint='0'*64
    proofs=nutrition_substitutions(t,result,q)
    if mode=='profile_missing':
        assert proofs and check_evidence(t,result=result,query=q).evidence_contract.missing_tools==['profile.get_summary']
    else:assert proofs==[]

def test_persisted_proof_is_revoked_if_source_observation_is_removed():
    _,t,result,q=archived();t=check_evidence(t,result=result,query=q)
    assert t.evidence_contract.substitutions
    t.observations=t.observations[1:]
    assert 'nutrition.list_history' in check_evidence(t).evidence_contract.missing_tools

@pytest.mark.parametrize('effect,kind,risk,domain',[('write','mutation','low','nutrition'),('read','assessment','high','nutrition'),('read','assessment','low','health')])
def test_other_authority_or_risk_never_refines(effect,kind,risk,domain):
    original=['nutrition_history','nutrition_today','health_screening']
    assert nutrition_read_evidence('评估昨天午餐。',domain=domain,kind=kind,effect=effect,risk=risk,evidence=original)==(original,[],[])
