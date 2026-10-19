"""R56 failures replayed through route checks and the real HTTP/agent graph."""
import copy, importlib, json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.tools import StructuredTool
from app.config import settings
from app.services import agent_runtime as runtime, agent_intent_model as model
from app.services.agent_intent import route_tools
from app.services.agent_intent_model import IntentRouteDecision, resolve_intent_with_fallback
from app.services.agent_tools import NoArguments, WorkoutProgressArguments
from app.services.business_clock import BUSINESS_DATE

ROWS=json.loads((Path(__file__).parent/'fixtures/round57/archived-delivery.json').read_text(encoding='utf8'))['rows']
READ_IDS=[r['id'] for r in ROWS if r['error']=='intent_understanding_unavailable']
LEAK_IDS=[r['id'] for r in ROWS if 'DSML' in (r['reply'] or '')]
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline-r57')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(settings,'AGENT_ENABLED',True)
    monkeypatch.setattr(model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external model')))
    yield
    BUSINESS_DATE.reset(token)

async def resolution(monkeypatch,r):
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(r['routes'][0])))
    return await resolve_intent_with_fallback(r['message'],use_model=True)

def install_tools(monkeypatch,r):
    calls=[]
    def build(db,*,user_id,allowlist):
        tools=[]
        for tool_id in allowlist:
            observed=next((t['result'] for t in r['tools'] if t['tool']==tool_id),{'found':False})
            async def invoke(_tool=tool_id,_result=observed,**args):
                calls.append((_tool,args));return copy.deepcopy(_result)
            tools.append(StructuredTool.from_function(coroutine=invoke,name=tool_id.replace('.','_'),description='Synthetic archived read',args_schema=WorkoutProgressArguments if tool_id=='workout.get_progress' else NoArguments))
        return tools
    monkeypatch.setattr(runtime,'build_read_tools',build)
    return calls

def scripted_http(monkeypatch,sequence):
    requests=[]
    for module in ('httpx','httpx2'):
        mod=importlib.import_module(module)
        original=mod.AsyncClient.send
        async def send(client,request,_mod=mod,_original=original,**kwargs):
            if request.url.host=='test':return await _original(client,request,**kwargs)
            assert request.url.host=='api.deepseek.com','Offline fixture cannot call another host'
            requests.append(json.loads(request.content))
            assert len(requests)<=len(sequence),'Unbounded model retry'
            message=copy.deepcopy(sequence[len(requests)-1])
            return _mod.Response(200,json={'id':'offline-'+str(len(requests)),'object':'chat.completion','model':'deepseek-flash','choices':[{'index':0,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop','message':message}],'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}},request=request)
        monkeypatch.setattr(mod.AsyncClient,'send',send)
    return requests

async def invoke(r,out):
    return await runtime.invoke_langchain_agent(None,user_id='synthetic-owner',history=[],user_message=r['message'],resolved_query=out.resolution.resolved_query,tool_allowlist=route_tools(out.resolution),resolution=out.resolution)

@pytest.mark.asyncio
@pytest.mark.parametrize('id',READ_IDS)
async def test_baseline_archived_readonly_is_not_mutation(id,monkeypatch):
    r=row(id)
    for payload in r['routes']:
        monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(payload)))
        out=await resolve_intent_with_fallback(r['message'],use_model=True)
        assert not out.understanding_failed,out.error_category
        assert out.resolution.requested_effect=='read' and out.attempt_count==1

@pytest.mark.asyncio
@pytest.mark.parametrize('id',LEAK_IDS)
async def test_baseline_protocol_text_is_not_a_completed_answer(id,monkeypatch):
    r=row(id);out=await resolution(monkeypatch,r);assert not out.understanding_failed
    calls=install_tools(monkeypatch,r)
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':r['reply']},{'role':'assistant','content':'离线协议恢复答复，仅验证执行链，不作为业务评测。'}])
    result=await invoke(r,out)
    assert 'DSML' not in result['messages'][-1].content
    assert len(requests)==2,'Exactly one bounded protocol recovery'
    assert len(calls)==len(r['tools']) and len(runtime._tool_call_records(result,'r57'))==len(calls)
    assert not requests[-1].get('tools'),'Protocol recovery cannot gain tool or write authority'
    assert runtime._usage_totals(result)==(24,10),'Failed generation and correction both cost tokens'

@pytest.mark.asyncio
async def test_baseline_two_optional_reads_have_graph_space_to_answer(monkeypatch):
    r=row('WX13-r2');out=await resolution(monkeypatch,r);assert not out.understanding_failed
    calls=install_tools(monkeypatch,r)
    seq=[{'role':'assistant','content':'','tool_calls':[{'id':'r57-'+str(i),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]} for i,(name,args) in enumerate([('profile_get_summary',{}),('workout_get_progress',{'weeks':4})])]
    seq.append({'role':'assistant','content':'离线图执行已收尾，不作为真实方案。'})
    requests=scripted_http(monkeypatch,seq)
    result=await invoke(r,out)
    assert len(requests)==3 and len(calls)==4
    assert result['messages'][-1].content=='离线图执行已收尾，不作为真实方案。'
    assert len(runtime._tool_call_records(result,'r57'))==4

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
    '把这份饮食方案保存为待确认提案。',
    '参考已保存计划给建议，然后保存这份饮食方案。',
    '查看本周完成次数后把今天训练标记为已完成。',
    '请完成当前训练。',
    '别只查看记录，请保存这份方案。',
    '不得不保存这份方案。',
    '不要不保存这份方案。',
    '不必不保存这份方案。',
    '先别不保存这份方案。',
    '不能不完成这次训练。',
    '请把计划完成日数改为3。',
    '查已保存体重，再记录今天体重72公斤。',
])
async def test_true_writes_cannot_use_readonly_route(message,monkeypatch):
    decision=IntentRouteDecision(intent_domain='workout_plan',request_kind='query',requested_effect='read',requested_output='answer',read_targets=['active_plan'],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.99)
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=decision))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert out.understanding_failed and not route_tools(out.resolution)

@pytest.mark.asyncio
@pytest.mark.parametrize('message',['不要保存这份方案，只解释它。','参考已保存计划给一周建议，不用保存方案。','已保存体重是多少？','先别把不完整的方案保存，请只给建议。'])
async def test_negative_save_and_saved_attributes_are_readonly(message,monkeypatch):
    decision=IntentRouteDecision(intent_domain='workout_plan',request_kind='query',requested_effect='read',requested_output='answer',read_targets=['active_plan'],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.99)
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=decision))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert not out.understanding_failed,out.error_category

@pytest.mark.asyncio
@pytest.mark.parametrize('limit,repeat_bad,code',[(1,False,'read_model_budget_exhausted'),(2,True,'read_response_protocol_invalid')])
async def test_protocol_failure_is_bounded_and_preserves_usage(monkeypatch,limit,repeat_bad,code):
    r=row('WX10-r1');out=await resolution(monkeypatch,r);calls=install_tools(monkeypatch,r)
    monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',limit)
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':r['reply']}]*(2 if repeat_bad else 1))
    result=await invoke(r,out)
    assert result['read_error_code']==code and 'DSML' not in result['messages'][-1].content
    assert len(calls)==2 and len(requests)==limit
    assert runtime._usage_totals(result)==(12*limit,5*limit)
    assert len(runtime._tool_call_records(result,'r57'))==2

@pytest.mark.asyncio
async def test_correction_never_executes_unknown_or_write_calls(monkeypatch):
    r=row('WX10-r1');out=await resolution(monkeypatch,r);calls=install_tools(monkeypatch,r)
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':r['reply']},{'role':'assistant','content':'','tool_calls':[{'id':'invented-write','type':'function','function':{'name':'delete_all_records','arguments':'{}'}}]}])
    result=await invoke(r,out)
    assert result['read_error_code']=='read_response_protocol_invalid'
    assert len(calls)==2 and not requests[-1].get('tools')
    assert len(runtime._tool_call_records(result,'r57'))==2
    assert result['read_generation_attempts']==2
    assert any(getattr(m,'tool_calls',None) and m.additional_kwargs.get('read_delivery_calls_not_executed') for m in result['messages'])

@pytest.mark.asyncio
async def test_graph_error_is_local_safe_stop_after_reads(monkeypatch):
    from langgraph.errors import GraphRecursionError
    from types import SimpleNamespace
    r=row('WX10-r1');out=await resolution(monkeypatch,r);calls=install_tools(monkeypatch,r)
    monkeypatch.setattr(runtime,'create_agent',lambda **kwargs:SimpleNamespace(ainvoke=AsyncMock(side_effect=GraphRecursionError('offline graph ceiling'))))
    result=await invoke(r,out)
    assert result['read_error_code']=='read_graph_step_limit' and len(calls)==2
    assert len(runtime._tool_call_records(result,'r57'))==2
    assert '网络' not in result['messages'][-1].content

@pytest.mark.asyncio
async def test_general_quoted_protocol_explanation_does_not_trigger_recovery(monkeypatch):
    from app.services.agent_intent import IntentResolution
    r=IntentResolution(primary_intent='general_qa',intent_domain='general',request_kind='query',requested_effect='read',risk_level='low',confidence=.99)
    text='用户提到的 <tool_call> 是一个标签。'
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':text}])
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message='解释标签',tool_allowlist=[],resolution=r)
    assert len(requests)==1 and result['messages'][-1].content==text

@pytest.mark.asyncio
async def test_repeated_model_calls_have_hard_budget(monkeypatch):
    from langchain_core.messages import AIMessage
    from test_round49_required_evidence import install,invoke as invoke_old,resolution as old_resolution
    monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',1)
    call=AIMessage(content='',tool_calls=[{'name':'nutrition_list_history','id':'one-history','args':{}}])
    calls=install(monkeypatch,responses=[call,AIMessage(content='should not dispatch')])
    result=await invoke_old(old_resolution())
    assert len(calls)==3 and result['read_error_code']=='read_model_budget_exhausted'

@pytest.mark.parametrize('controller_rejected',[False,True])
def test_unexecuted_request_is_not_a_successful_tool_audit(controller_rejected):
    from langchain_core.messages import AIMessage,ToolMessage
    request=AIMessage(content='',tool_calls=[{'name':'profile_get_summary','id':'never-ran','args':{}}])
    messages=[request]
    if controller_rejected:
        messages.append(ToolMessage(content='budget rejection, no invocation',status='error',name='profile_get_summary',tool_call_id='never-ran',additional_kwargs={'executed':False}))
    assert runtime._tool_call_records({'messages':messages},'r57-unexecuted')==[]
