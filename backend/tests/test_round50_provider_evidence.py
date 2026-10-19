"""Actual LangChain/OpenAI HTTP serialization, not a fake model response only."""
import importlib,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.config import settings
from app.services import agent_runtime as runtime,agent_intent_model
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.services.agent_intent import route_tools
from test_round49_required_evidence import install,resolution,invoke
from app.services.business_clock import BUSINESS_DATE

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline')
    monkeypatch.setattr(settings,'AGENT_ENABLED',True)
    monkeypatch.setattr(agent_intent_model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external model allowed')))
    yield
    BUSINESS_DATE.reset(token)

def http_provider(monkeypatch,*,fail_at=None):
    requests=[]
    for module in ('httpx','httpx2'):
        mod=importlib.import_module(module)
        original=mod.AsyncClient.send
        async def send(client,request,_mod=mod,_original=original,**kwargs):
            if request.url.host in ('test','synthetic-test'):
                return await _original(client,request,**kwargs)
            assert request.url.host=='api.deepseek.com','Offline test must not use any other host'
            body=json.loads(request.content);requests.append(body)
            history=body['messages']
            if (body.get('thinking',{}).get('type')!='disabled'
                    and any(m['role']=='assistant' and 'reasoning_content' not in m for m in history)):
                return _mod.Response(400,json={'error':{'message':'The reasoning_content in the thinking mode must be passed back','type':'invalid_request_error'}},request=request)
            if fail_at and len(requests)>=fail_at:
                return _mod.Response(400,json={'error':{'message':'synthetic provider rejected request','type':'invalid_request_error'}},request=request)
            has_history=any(m['role']=='tool' and m.get('name')=='nutrition_list_history' for m in history)
            # OpenAI may omit name on ToolMessages; match the chosen call id too.
            has_history |= any(m['role']=='tool' and m.get('tool_call_id')=='history-read' for m in history)
            if has_history:
                message={'role':'assistant','content':'已根据指定日期和资料核对记录。'};finish='stop'
            else:
                message={'role':'assistant','content':'','tool_calls':[{'type':'function','id':'history-read','function':{'name':'nutrition_list_history','arguments':json.dumps({'start_date':'2026-10-08','end_date':'2026-10-08'})}}]};finish='tool_calls'
            return _mod.Response(200,json={'id':'offline-response-'+str(len(requests)),'object':'chat.completion','model':'deepseek-flash','choices':[{'index':0,'finish_reason':finish,'message':message}],'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}},request=request)
        monkeypatch.setattr(mod.AsyncClient,'send',send)
    return requests

def install_http(monkeypatch,*,fail_at=None):
    build=runtime._build_model
    calls=install(monkeypatch)
    monkeypatch.setattr(runtime,'_build_model',build)
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(settings,'AGENT_ENABLED',True)
    return calls,http_provider(monkeypatch,fail_at=fail_at)

@pytest.mark.asyncio
@pytest.mark.parametrize('history',[[],[{'role':'user','content':'先看我的体重'},{'role':'assistant','content':'已有资料中体重65kg。'}]])
async def test_http_mode_and_full_model_tool_continuation(monkeypatch,history):
    calls,requests=install_http(monkeypatch)
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic-owner',history=history,user_message='评估昨天的记录',tool_allowlist=['profile.get_summary','nutrition.get_recent_context','nutrition.list_history'],resolution=resolution())
    assert len(requests)==2 and all(r['thinking']=={'type':'disabled'} for r in requests)
    assert [x[0] for x in calls]==['profile.get_summary','nutrition.get_recent_context','nutrition.list_history']
    assert len(runtime._tool_call_records(result,'run'))==3
    assert result['messages'][-1].content=='已根据指定日期和资料核对记录。'
    assert all('reasoning_content' not in m for r in requests for m in r['messages'])
    for old in history:assert any(m.get('content')==old['content'] for m in requests[0]['messages'])

@pytest.mark.asyncio
@pytest.mark.parametrize('fail_at,count',[(1,2),(2,3)])
async def test_provider_failure_carries_prefetch_and_model_tool_evidence(monkeypatch,fail_at,count):
    calls,requests=install_http(monkeypatch,fail_at=fail_at)
    with pytest.raises(Exception) as info:await invoke(resolution())
    partial=getattr(info.value,'partial_result',None)
    assert partial is not None,'Provider failure must not erase completed reads'
    records=runtime._tool_call_records(partial,'run')
    assert len(records)==count and len(calls)==count
    assert all(r.status=='completed' for r in records)
    assert getattr(info.value,'provider_error_category',None)=='BadRequestError'

DATA=json.loads((Path(__file__).parent/'fixtures/round50/archived-na15-routes.json').read_text(encoding='utf8'))['rows']
@pytest.mark.asyncio
@pytest.mark.parametrize('row',DATA,ids=lambda r:r['id'])
async def test_archived_read_assessment_is_not_a_record_write(monkeypatch,row):
    for payload in row['routes']:
        monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(payload)))
        out=await resolve_intent_with_fallback(row['message'],use_model=True)
        assert not out.understanding_failed,out.error_category
        assert out.attempt_count==1 and out.resolution.requested_effect=='read'
        assert 'profile.get_summary' in route_tools(out.resolution)

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
    '请记录今天午餐米饭100克。',
    '先评价营养和记录情况，然后记录晚餐米饭100克。',
    '查看记录情况后删除昨天午餐。',
    '把午餐的营养和记录情况保存下来。',
    '帮我记录情况，并补录昨天午餐米饭100克。',
])
async def test_nominal_record_fix_preserves_real_writes(monkeypatch,message):
    raw=IntentRouteDecision(intent_domain='nutrition',request_kind='assessment',requested_effect='read',requested_output='answer',read_targets=['nutrition_today'],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.99)
    monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=raw))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert out.understanding_failed and not route_tools(out.resolution)
