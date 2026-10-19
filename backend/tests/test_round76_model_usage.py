"""HTTP observations include structured calls, SDK retries and missing usage."""
import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services import agent_runtime as runtime
from app.services.agent_daily_meal_plans import _model as daily_model
from app.services.ai_client import structured_chat_completion, StructuredAIServiceError
from app.services.agent_model_usage import observe_model_usage, ObservedAsyncClient, model_http_options


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setattr(settings, 'AGENT_ENABLED', True)
    monkeypatch.setattr(settings, 'DEEPSEEK_API_KEY', 'synthetic-offline')
    monkeypatch.setattr(settings, 'DEEPSEEK_BASE_URL', 'https://api.deepseek.com')


def response(usage=None, content='ok'):
    body={'id':'synthetic','object':'chat.completion','model':'synthetic',
          'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':content}}]}
    if usage is not None:body['usage']=usage
    return body


def transport(monkeypatch, replies):
    requests=[]
    async def send(client, request, **kwargs):
        assert request.url.host=='api.deepseek.com'
        requests.append(request)
        value=replies.pop(0)
        if isinstance(value, BaseException):raise value
        status,body=value
        return httpx.Response(status,json=body,request=request)
    # R77 calibration: inject faults at the actual transport boundary. A
    # synthetic exception at send entry cannot prove any transport occurred.
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',send)
    return requests


async def structured():
    return await structured_chat_completion([{'role':'user','content':'synthetic'}],model='synthetic',max_tokens=30,temperature=0,
        function_name='synthetic',function_description='synthetic',json_schema={'type':'object','properties':{},'additionalProperties':False},json_example={})


@pytest.mark.asyncio
async def test_strict_fallback_counts_failed_http_without_fabricating_zero_tokens(monkeypatch):
    requests=transport(monkeypatch,[(404,{'error':'strict unsupported'}),(200,response({'prompt_tokens':12,'completion_tokens':5},'{}'))])
    async with observe_model_usage(1) as ledger:
        result=await structured();assert result.payload=={}
        usage=ledger.snapshot(complete=True)
    assert len(requests)==usage.http_requests==2
    assert usage.http_errors==1 and usage.http_successes==1 and usage.usage_known_requests==1
    assert (usage.known_input_tokens,usage.known_output_tokens)==(12,5)
    assert usage.input_tokens is None and usage.output_tokens is None


@pytest.mark.asyncio
async def test_protocol_fallback_counts_both_paid_responses(monkeypatch):
    transport(monkeypatch,[(200,response({'prompt_tokens':12,'completion_tokens':5},'not a tool envelope')),
                           (200,response({'prompt_tokens':20,'completion_tokens':7},'{}'))])
    async with observe_model_usage(1) as ledger:
        await structured();usage=ledger.snapshot(complete=True)
    assert (usage.http_requests,usage.input_tokens,usage.output_tokens)==(2,32,12)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[500,'timeout'])
async def test_real_sdk_retry_is_a_second_http_attempt(monkeypatch,failure):
    replies=[httpx.ReadTimeout('synthetic') if failure=='timeout' else (500,{'error':{'message':'synthetic','type':'server_error'}}),
             (200,response({'prompt_tokens':13,'completion_tokens':6}))]
    requests=transport(monkeypatch,replies)
    async with observe_model_usage(1) as ledger:
        answer=await runtime._build_model().ainvoke('synthetic')
        assert answer.content=='ok'
        client=model_http_options()['http_async_client']
        usage=ledger.snapshot(complete=True)
        assert not client.is_closed
    assert client.is_closed and len(requests)==usage.http_requests==2
    assert usage.http_errors+usage.transport_errors==1
    assert usage.known_input_tokens==13 and usage.input_tokens is None


@pytest.mark.asyncio
async def test_daily_model_and_regular_model_share_ledger_not_clients(monkeypatch):
    transport(monkeypatch,[(200,response({'prompt_tokens':10,'completion_tokens':2})),(200,response({'prompt_tokens':20,'completion_tokens':3}))])
    async with observe_model_usage(1) as ledger:
        await runtime._build_model().ainvoke('synthetic')
        await daily_model().ainvoke('synthetic')
        usage=ledger.snapshot(complete=True)
    assert [c.source for c in usage.calls]==['agent','daily_meal']
    assert (usage.input_tokens,usage.output_tokens)==(30,5)
    assert all(c.is_closed for c in ledger.clients.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('usage,expected',[
    (None,(None,None,None)),({},(None,None,None)),
    ({'prompt_tokens':0,'completion_tokens':0},(0,0,None)),
    ({'prompt_tokens':True,'completion_tokens':5},(None,5,None)),
    ({'prompt_tokens':'12','completion_tokens':5},(None,5,None)),
    ({'prompt_tokens':12.2,'completion_tokens':-1},(None,None,None)),
    ({'prompt_tokens':12,'completion_tokens':5,'prompt_cache_hit_tokens':8},(12,5,8)),
    ({'prompt_tokens':12,'completion_tokens':5,'prompt_tokens_details':{'cached_tokens':9}},(12,5,9)),
    ({'prompt_tokens':12,'completion_tokens':5,'prompt_cache_hit_tokens':99},(12,5,None)),
])
async def test_missing_malformed_and_cache_usage_are_not_invented(monkeypatch,usage,expected):
    transport(monkeypatch,[(200,response(usage))])
    async with observe_model_usage(1) as ledger:
        async with ObservedAsyncClient(source='chat') as client:await client.post('https://api.deepseek.com/chat/completions')
        snap=ledger.snapshot(complete=True);call=snap.calls[0]
    assert (call.input_tokens,call.output_tokens,call.cached_input_tokens)==expected
    assert (snap.input_tokens is not None)==(expected[0] is not None and expected[1] is not None)


@pytest.mark.asyncio
async def test_concurrent_task_isolation_and_no_private_payload(monkeypatch):
    barrier=asyncio.Event();seen=0
    async def send(client,request,**kwargs):
        nonlocal seen
        seen+=1
        if seen==2:barrier.set()
        await barrier.wait()
        n=int(request.headers['synthetic-count'])
        return httpx.Response(200,json=response({'prompt_tokens':n,'completion_tokens':1}),request=request)
    monkeypatch.setattr(httpx.AsyncClient,'send',send)
    async def worker(n):
        async with observe_model_usage(1) as ledger:
            async with ObservedAsyncClient(source='chat') as client:
                await client.post('https://api.deepseek.com/chat/completions',headers={'synthetic-count':str(n),'Authorization':'never-persist-this'},json={'private':'never-persist-this'})
            return ledger.snapshot(complete=True)
    snapshots=await asyncio.gather(worker(11),worker(22))
    assert [s.input_tokens for s in snapshots]==[11,22]
    assert all(s.http_requests==1 and 'never-persist-this' not in s.model_dump_json() for s in snapshots)
    assert model_http_options()=={}


@pytest.mark.asyncio
async def test_reclaimed_attempt_known_subtotal_is_not_full_run_cost(monkeypatch):
    transport(monkeypatch,[(200,response({'prompt_tokens':12,'completion_tokens':5}))])
    async with observe_model_usage(2) as ledger:
        async with ObservedAsyncClient(source='chat') as client:await client.post('https://api.deepseek.com/chat/completions')
        snap=ledger.snapshot(complete=True)
    assert snap.prior_attempts_unobserved and snap.worker_attempt==2
    assert snap.input_tokens is None and snap.known_input_tokens==12


@pytest.mark.asyncio
async def test_cancelled_request_stays_unknown_and_context_resets(monkeypatch):
    transport(monkeypatch,[asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        async with observe_model_usage(1) as ledger:
            async with ObservedAsyncClient(source='chat') as client:await client.post('https://api.deepseek.com/chat/completions')
    snap=ledger.snapshot(complete=True)
    assert snap.pending_or_cancelled==1 and not snap.observation_complete and snap.input_tokens is None
    assert model_http_options()=={} and not ledger.active


@pytest.mark.asyncio
async def test_rule_only_run_zero_is_measured_not_missing():
    async with observe_model_usage(1) as ledger:
        assert ledger.snapshot(complete=False).input_tokens is None
        snap=ledger.snapshot(complete=True)
        assert snap.http_requests==snap.input_tokens==snap.output_tokens==0


@pytest.mark.asyncio
async def test_structured_terminal_failure_is_observed(monkeypatch):
    transport(monkeypatch,[(402,{'error':'synthetic'})])
    async with observe_model_usage(1) as ledger:
        with pytest.raises(StructuredAIServiceError):await structured()
        snap=ledger.snapshot(complete=True)
    assert snap.http_requests==snap.http_errors==1 and snap.input_tokens is None
