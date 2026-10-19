"""Independent transport evidence, local refusals and legacy metric compatibility."""
import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import TypeAdapter

from app.schemas.agent_model_usage import AgentModelUsagePayload, LegacyAgentModelUsage
from app.services.agent_model_usage import observe_model_usage, ObservedAsyncClient, observed_post
from app.services import agent_runtime as runtime
from test_round76_model_usage import response


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'DEEPSEEK_API_KEY', 'synthetic-only')
    monkeypatch.setattr(settings, 'DEEPSEEK_BASE_URL', 'https://synthetic.invalid')


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['sdk_client', 'structured'])
async def test_six_client_calls_four_responses_two_local_refusals(monkeypatch, path):
    seen=[]
    original=httpx.AsyncClient.send
    async def guard(client, request, **kwargs):
        if len(seen)==4:raise RuntimeError('local budget stopped; private text')
        return await original(client, request, **kwargs)
    async def provider(request):
        status=[200,200,503,503][len(seen)];seen.append(status)
        return httpx.Response(status,json=response({'prompt_tokens':10,'completion_tokens':2}) if status==200 else {'error':'busy'})
    monkeypatch.setattr(httpx.AsyncClient,'send',guard)
    async with observe_model_usage(1) as ledger:
        client=ObservedAsyncClient(source='agent',transport=httpx.MockTransport(provider)) if path=='sdk_client' else httpx.AsyncClient(transport=httpx.MockTransport(provider))
        async with client:
            for _ in range(6):
                try:
                    if path=='sdk_client':await client.post('https://synthetic.invalid/')
                    else:await observed_post(client,'https://synthetic.invalid/',source='structured')
                except RuntimeError:pass
        snap=ledger.snapshot(complete=True)
    assert seen==[200,200,503,503]
    assert (snap.client_attempts,snap.transport_attempts,snap.http_requests,snap.http_responses)==(6,4,4,4)
    assert (snap.http_successes,snap.http_errors,snap.local_errors,snap.transport_errors)==(2,2,2,0)
    assert snap.input_tokens is None and snap.known_input_tokens==20
    assert 'private text' not in snap.model_dump_json()


@pytest.mark.asyncio
async def test_actual_model_sdk_retry_blocked_before_transport(monkeypatch):
    original=httpx.AsyncClient.send;seen=[]
    async def guard(client,request,**kw):
        if seen:raise RuntimeError('synthetic budget refusal')
        return await original(client,request,**kw)
    async def provider(transport,request):
        seen.append(request.url.path)
        return httpx.Response(503,json={'error':{'message':'busy','type':'server_error'}},request=request)
    monkeypatch.setattr(httpx.AsyncClient,'send',guard)
    monkeypatch.setattr(httpx.AsyncHTTPTransport,'handle_async_request',provider)
    async with observe_model_usage(1) as ledger:
        with pytest.raises(Exception):await runtime._build_model().ainvoke('synthetic')
        snap=ledger.snapshot(complete=True)
    assert len(seen)==1 and snap.http_errors==snap.http_responses==snap.transport_attempts==1
    assert snap.client_attempts>1 and snap.local_errors==snap.client_attempts-1
    assert snap.transport_errors==0 and snap.input_tokens is None


@pytest.mark.asyncio
@pytest.mark.parametrize('error',[httpx.ConnectError,httpx.ConnectTimeout,httpx.ReadTimeout,httpx.WriteError,httpx.RemoteProtocolError])
async def test_transport_fault_is_not_misclassified_as_local(error):
    async def fail(request):raise error('synthetic transport fault')
    async with observe_model_usage(1) as ledger:
        async with ObservedAsyncClient(source='agent',transport=httpx.MockTransport(fail)) as client:
            with pytest.raises(error):await client.get('https://synthetic.invalid/')
        snap=ledger.snapshot(complete=True)
    assert snap.transport_attempts==snap.http_requests==snap.transport_errors==1
    assert snap.http_responses==snap.local_errors==0 and snap.input_tokens is None


@pytest.mark.asyncio
async def test_local_only_errors_can_have_measured_zero_cost():
    async def reject(request):raise ValueError('local hook refusal')
    async with observe_model_usage(1) as ledger:
        async with ObservedAsyncClient(source='chat',event_hooks={'request':[reject]}) as client:
            with pytest.raises(ValueError):await client.get('https://synthetic.invalid/')
        snap=ledger.snapshot(complete=True)
    assert snap.local_errors==snap.client_attempts==1
    assert snap.http_requests==snap.transport_attempts==snap.input_tokens==snap.output_tokens==0


@pytest.mark.asyncio
async def test_uninstrumentable_custom_client_error_stays_unknown():
    class CustomClient:
        async def post(self,*args,**kwargs):raise RuntimeError('cannot prove if sent')
    async with observe_model_usage(1) as ledger:
        with pytest.raises(RuntimeError):await observed_post(CustomClient(),'opaque',source='chat')
        snap=ledger.snapshot(complete=True)
    assert snap.unclassified_client_errors==1 and snap.local_errors==snap.transport_errors==0
    assert snap.input_tokens is None and not snap.calls[0].transport_boundary_observed


@pytest.mark.asyncio
async def test_redirect_responses_do_not_disappear_from_cost_or_request_counts():
    seen=[]
    async def provider(request):
        seen.append(str(request.url))
        if len(seen)==1:return httpx.Response(307,headers={'location':'/second'})
        return httpx.Response(200,json=response({'prompt_tokens':8,'completion_tokens':3}))
    async with observe_model_usage(1) as ledger:
        async with ObservedAsyncClient(source='agent',transport=httpx.MockTransport(provider),follow_redirects=True) as client:
            result=await client.post('https://synthetic.invalid/first')
        snap=ledger.snapshot(complete=True)
    assert result.status_code==200 and len(seen)==2
    assert snap.client_attempts==1 and snap.http_requests==snap.http_responses==snap.transport_attempts==2
    assert [r.http_status for r in snap.calls[0].responses]==[307,200]
    assert snap.input_tokens is None and snap.known_input_tokens==8


@pytest.mark.asyncio
async def test_stream_is_never_consumed_for_telemetry():
    consumed=[]
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            consumed.append(True);yield b'{"usage":{"prompt_tokens":9,"completion_tokens":2}}'
    async def provider(request):return httpx.Response(200,stream=Stream())
    async with observe_model_usage(1) as ledger:
        async with ObservedAsyncClient(source='agent',transport=httpx.MockTransport(provider)) as client:
            result=await client.send(client.build_request('GET','https://synthetic.invalid/'),stream=True)
            assert not consumed
            assert ledger.snapshot(complete=True).input_tokens is None
            await result.aclose()


@pytest.mark.asyncio
async def test_mounted_transports_selected_and_closed_with_original_semantics():
    paths=[];closed=[]
    class Transport(httpx.AsyncBaseTransport):
        def __init__(self,name):self.name=name
        async def handle_async_request(self,request):
            paths.append(self.name);return httpx.Response(200,json=response({'prompt_tokens':1,'completion_tokens':0}))
        async def aclose(self):closed.append(self.name)
    async with observe_model_usage(1) as ledger:
        async with ObservedAsyncClient(source='chat',transport=Transport('default'),mounts={'https://special.invalid':Transport('special')},trust_env=False) as client:
            await client.get('https://synthetic.invalid/')
            await client.get('https://special.invalid/')
        snap=ledger.snapshot(complete=True)
    assert paths==['default','special'] and sorted(closed)==['default','special']
    assert snap.transport_attempts==snap.http_responses==snap.input_tokens==2


@pytest.mark.asyncio
async def test_custom_transport_context_exit_receives_original_exception():
    events=[]
    class Transport(httpx.AsyncBaseTransport):
        async def __aenter__(self):events.append('enter');return self
        async def __aexit__(self,kind,value,traceback):events.append(kind)
        async def handle_async_request(self,request):return httpx.Response(200,json=response({'prompt_tokens':1,'completion_tokens':0}))
    async with observe_model_usage(1):
        with pytest.raises(ValueError):
            async with ObservedAsyncClient(source='chat',transport=Transport()) as client:
                await client.get('https://synthetic.invalid/')
                raise ValueError('caller failure')
    assert events==['enter',ValueError]


@pytest.mark.asyncio
async def test_shared_client_concurrent_scopes_do_not_leak():
    gate=asyncio.Event();seen=[]
    async def provider(request):
        n=int(request.url.path[1:]);seen.append(n)
        if len(seen)==2:gate.set()
        await gate.wait()
        return httpx.Response(200,json=response({'prompt_tokens':n,'completion_tokens':1}))
    async with ObservedAsyncClient(source='chat',transport=httpx.MockTransport(provider)) as client:
        async def worker(n):
            async with observe_model_usage(1) as ledger:
                await client.get(f'https://synthetic.invalid/{n}')
                return ledger.snapshot(complete=True)
        snapshots=await asyncio.gather(worker(11),worker(22))
    assert [s.input_tokens for s in snapshots]==[11,22]
    assert all(s.client_attempts==s.transport_attempts==s.http_requests==1 for s in snapshots)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['before','transport'])
async def test_cancellation_remains_unknown_and_is_not_transport_error(phase):
    async def cancel(request):raise asyncio.CancelledError()
    options={'event_hooks':{'request':[cancel]}} if phase=='before' else {'transport':httpx.MockTransport(cancel)}
    async with observe_model_usage(1) as ledger:
        async with ObservedAsyncClient(source='agent',**options) as client:
            with pytest.raises(asyncio.CancelledError):await client.get('https://synthetic.invalid/')
        snap=ledger.snapshot(complete=True)
    assert snap.pending_or_cancelled==1 and not snap.observation_complete and snap.input_tokens is None
    assert snap.transport_attempts==(phase=='transport') and snap.transport_errors==snap.local_errors==0


@asynccontextmanager
async def local_server(handler):
    tasks=set()
    async def serve(reader,writer):
        tasks.add(asyncio.current_task())
        try:await handler(reader,writer)
        finally:
            writer.close();await writer.wait_closed();tasks.discard(asyncio.current_task())
    server=await asyncio.start_server(serve,'127.0.0.1',0)
    try:yield 'http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
    finally:
        server.close();await server.wait_closed()
        for task in list(tasks):task.cancel()
        if tasks:await asyncio.gather(*list(tasks),return_exceptions=True)


@pytest.mark.asyncio
async def test_real_loopback_http_response_and_body_usage():
    received=[]
    async def handler(reader,writer):
        received.append(await reader.readuntil(b'\r\n\r\n'))
        body=b'{"usage":{"prompt_tokens":17,"completion_tokens":4}}'
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
        await writer.drain()
    async with local_server(handler) as url:
        async with observe_model_usage(1) as ledger:
            async with ObservedAsyncClient(source='chat',trust_env=False) as client:await client.get(url)
            snap=ledger.snapshot(complete=True)
    assert len(received)==snap.http_requests==snap.http_responses==snap.transport_attempts==1
    assert (snap.input_tokens,snap.output_tokens)==(17,4)


@pytest.mark.asyncio
async def test_real_response_headers_then_body_timeout_keeps_both_facts():
    async def handler(reader,writer):
        await reader.readuntil(b'\r\n\r\n')
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{');await writer.drain()
        # Bound the synthetic server independently of the client timeout.
        await asyncio.sleep(.5)
    async with local_server(handler) as url:
        async with observe_model_usage(1) as ledger:
            async with ObservedAsyncClient(source='chat',trust_env=False,timeout=.1) as client:
                with pytest.raises(httpx.ReadTimeout):await client.get(url)
            snap=ledger.snapshot(complete=True)
    assert snap.http_requests==snap.http_successes==snap.transport_errors==1
    assert snap.http_responses==1 and snap.local_errors==0 and snap.input_tokens is None


def test_v1_metric_read_preserves_historical_semantics():
    old={'version':'provider_http_v1','scope':'worker_attempt','worker_attempt':1,'prior_attempts_unobserved':False,
         'observation_complete':True,'http_requests':6,'http_successes':2,'http_errors':2,'transport_errors':2,
         'pending_or_cancelled':0,'usage_known_requests':2,'known_input_tokens':20,'known_output_tokens':4,
         'input_tokens':None,'output_tokens':None,'calls':[]}
    parsed=TypeAdapter(AgentModelUsagePayload).validate_python(old)
    assert isinstance(parsed,LegacyAgentModelUsage) and parsed.model_dump(mode='json')==old
