"""Recorded R59 failures; no external provider calls in these mechanism tests."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.config import settings
from app.services import agent_intent_model as routing, agent_runtime as runtime
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.services.agent_intent import route_tools
from app.services.business_clock import BUSINESS_DATE
from app.services.history_status_scope import history_status_problem
from test_round57_read_delivery import scripted_http,install_tools
from test_round59_read_intent_ledger import routes

ROOT=Path(__file__).parent/'fixtures/round60'
def archived(id):return json.loads((ROOT/(id+'.json')).read_text(encoding='utf8'))
def probe(id):
    r=archived(id)
    return {'message':r['turns'][0]['message'],'routes':routes(r),'tools':r['tool_attempts']}

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline-r60')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(routing,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external model')))
    yield
    BUSINESS_DATE.reset(token)

async def resolve(monkeypatch,r):
    monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(r['routes'][0])))
    return await resolve_intent_with_fallback(r['message'],use_model=True)

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['WX26-r1','WX26-r2'])
async def test_baseline_onset_is_not_start_workout_or_health_write(id,monkeypatch):
    r=probe(id)
    for raw in r['routes']:
        monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
        out=await resolve_intent_with_fallback(r['message'],use_model=True)
        assert not out.understanding_failed,out.error_category
        assert out.resolution.requested_effect=='read' and not out.resolution.change_requests
        assert out.resolution.risk_level=='medium'

@pytest.mark.asyncio
async def test_baseline_paraphrased_excluded_states_keep_completed_scope(monkeypatch):
    r=probe('CW19-r2');out=await resolve(monkeypatch,r)
    assert not out.understanding_failed
    assert not history_status_problem(out.resolution,r['message'])

def budget_sequence():
    sequence=[]
    for cid,name,args in [('profile','profile_get_summary',{}),('invalid','workout_list_history',{'limit':10}),('budget','workout_get_progress',{'weeks':4})]:
        sequence.append({'role':'assistant','content':'','tool_calls':[{'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]})
    sequence.append({'role':'assistant','content':'已取得计划和健康资料；根据这些证据提供一周临时建议。没有取得额外训练进度，不对它作判断。'})
    return sequence

async def budget_probe(monkeypatch):
    r=probe('WX04-r2');out=await resolve(monkeypatch,r);assert not out.understanding_failed
    monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',4)
    monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',4)
    calls=install_tools(monkeypatch,r);seq=budget_sequence();requests=scripted_http(monkeypatch,seq)
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=r['message'],resolved_query=out.resolution.resolved_query,tool_allowlist=route_tools(out.resolution),resolution=out.resolution)
    return result,calls,requests,seq

@pytest.mark.asyncio
async def test_baseline_budget_denial_is_audited_without_function_execution(monkeypatch):
    result,calls,requests,_=await budget_probe(monkeypatch)
    records=runtime._tool_call_records(result,'round60')
    assert len(calls)==3
    denied=[x for x in records if x.call_id=='budget']
    assert len(denied)==1,'A real budget decision needs a durable record'
    assert denied[0].status=='failed' and denied[0].error_code=='read_tool_budget_exhausted'
    assert denied[0].result_data['executed'] is False

@pytest.mark.asyncio
async def test_baseline_budget_can_finish_from_evidence_without_more_tools(monkeypatch):
    result,calls,requests,seq=await budget_probe(monkeypatch)
    assert result['messages'][-1].content==seq[-1]['content']
    assert not result.get('read_error_code')
    assert len(requests)==4 and not requests[-1].get('tools') and len(calls)==3
    assert runtime._usage_totals(result)==(48,20)
    assert result['read_budget_recovery_attempts']==1

@pytest.mark.asyncio
async def test_baseline_new_symptom_has_source_bound_generation_contract(monkeypatch):
    r=probe('WX24-r1');out=await resolve(monkeypatch,r);install_tools(monkeypatch,r)
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':'仅测试上下文传递，不计业务成功。'}])
    await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=r['message'],resolved_query=out.resolution.resolved_query,tool_allowlist=route_tools(out.resolution),resolution=out.resolution)
    system='\n'.join(m['content'] for m in requests[0]['messages'] if m['role']=='system')
    assert 'current_symptom_evidence' in system and '今天膝盖突然疼' in system
    assert '不得把替代动作称为无负荷' in system

@pytest.mark.parametrize('message',[
    '今天手腕开始疼，请说明怎么调整训练，不保存。',
    '肩膀开始有点不适，请解释如何调整。',
    '肘部开始肿胀，请给训练建议，不记录。',
])
def test_inchoative_state_is_not_a_command(message):
    from app.services.agent_intent import resolve_intent
    assert resolve_intent(message).requested_effect=='read'

@pytest.mark.parametrize('message',[
    '肘部开始疼，请开始今天训练。',
    '今天手腕开始疼，请把它记录到健康档案。',
    '今天肩膀开始不适，请把卧推改为2组。',
    '请开始训练，出现疼痛就停止。',
])
def test_state_mask_does_not_remove_independent_execution(message):
    from app.services.agent_intent import resolve_intent
    assert resolve_intent(message).request_kind=='mutation'

@pytest.mark.parametrize('suffix',[
    '不把提前结束和进行中的场次计入',
    '不要把跳过与放弃的训练场次计入',
    '进行中的训练场次不要计入',
])
def test_postfix_and_object_exclusions_keep_scope(suffix):
    from app.services.history_status_scope import parse_history_status_scope
    scope=parse_history_status_scope('只查本周已完成训练场次；'+suffix)
    assert scope.complete and scope.statuses==('completed',)

@pytest.mark.parametrize('suffix',[
    '不把已完成的场次计入', '不是不把进行中的场次计入',
    '不要不把进行中的场次计入', '不把某些状态计入',
])
def test_conflicting_unknown_and_double_negative_exclusion_remains_unresolved(suffix):
    from app.services.history_status_scope import parse_history_status_scope
    assert not parse_history_status_scope('只查已完成场次；'+suffix).complete

@pytest.mark.parametrize('message',[
    '今天膝盖不疼了，给训练建议。', '今天没有明显疼痛，怎么训练？',
    '如果今天膝盖疼，应该怎样训练？', '朋友今天膝盖疼，我想了解训练。',
    '解释“今天膝盖突然疼”这句话。', '以前膝痛，今天已经恢复，怎么训练？',
])
def test_negated_historical_hypothetical_and_quoted_symptoms_are_not_current(message):
    from app.services.agent_symptom_context import current_symptom_quotes
    assert current_symptom_quotes(message)==[]

@pytest.mark.parametrize('message',[
    '今天手腕开始疼，怎么训练？',
    '档案有膝痛，但今天踝部突然不适，请给训练建议。',
    '膝盖突然疼，走路也疼，请给临时安排。',
])
def test_current_symptom_is_quoted_without_inventing_diagnosis(message):
    from app.services.agent_symptom_context import current_symptom_quotes
    quotes=current_symptom_quotes(message)
    assert quotes and all(q in message for q in quotes)

@pytest.mark.asyncio
@pytest.mark.parametrize('has_evidence,allowed,remaining',[(False,True,1),(True,False,1),(True,True,0)])
async def test_budget_exit_no_unbounded_or_unauthorized_generation(has_evidence,allowed,remaining):
    from langchain_core.messages import AIMessage,ToolMessage
    from app.services.agent_read_delivery import ReadDeliveryBudget,recover_budget_answer
    model=AsyncMock();budget=ReadDeliveryBudget(remaining)
    messages=[ToolMessage(content='{}',tool_call_id='e',name='plan_get_active')] if has_evidence else []
    messages.append(AIMessage(content='controller stop'))
    result=await recover_budget_answer({'messages':messages},model=model,budget=budget,query='q',history=[],system_prompt='s',allowed=allowed)
    assert result['read_error_code']=='read_tool_budget_exhausted'
    assert ('已取得' in result['messages'][-1].content)==has_evidence
    model.ainvoke.assert_not_awaited()

@pytest.mark.asyncio
async def test_budget_recovery_cannot_execute_a_new_native_call(monkeypatch):
    r=probe('WX04-r2');out=await resolve(monkeypatch,r);calls=install_tools(monkeypatch,r)
    seq=budget_sequence();seq[-1]={'role':'assistant','content':'','tool_calls':[{'id':'fake-write','type':'function','function':{'name':'save_plan','arguments':'{}'}}]}
    requests=scripted_http(monkeypatch,seq)
    result=await runtime.invoke_langchain_agent(None,user_id='s',history=[],user_message=r['message'],tool_allowlist=route_tools(out.resolution),resolution=out.resolution)
    assert result['read_error_code']=='read_response_protocol_invalid'
    assert len(calls)==3 and len(requests)==4 and not requests[-1].get('tools')
    assert len(runtime._tool_call_records(result,'r60'))==5
    assert not any(x.call_id=='fake-write' for x in runtime._tool_call_records(result,'r60'))

def test_budget_batch_rejections_each_get_audited():
    from langchain_core.messages import AIMessage
    from app.services.agent_required_evidence import EvidenceToolLimit
    message=AIMessage(content='',tool_calls=[{'id':str(i),'name':'profile_get_summary','args':{},'type':'tool_call'} for i in range(3)])
    gate=EvidenceToolLimit(2);result=gate.after_model({'messages':[message]},None)
    records=runtime._tool_call_records({'messages':[message,*result['messages']]},'r60')
    assert gate.exhausted and len(records)==3
    assert all(r.status=='failed' and r.result_data['executed'] is False for r in records)
    assert all(r.result_data['batch_call_ids']==['0','1','2'] for r in records)
