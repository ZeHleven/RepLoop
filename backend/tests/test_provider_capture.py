import importlib, json
import pytest
from evals.provider_capture import ProviderCapture

@pytest.mark.asyncio
@pytest.mark.parametrize('module',['httpx','httpx2'])
async def test_response_and_retry_are_transparent_and_headers_absent(module):
    mod=importlib.import_module(module); rows={}; calls=0
    def handler(request):
        nonlocal calls
        calls+=1
        return mod.Response(503 if calls==1 else 200,json={'retry':calls,'usage':{'prompt_tokens':3,'completion_tokens':2}},headers={'x-secret':'response-secret'})
    capture=ProviderCapture(lambda r:rows.update({r['index']:dict(r)}))
    capture.install()
    try:
        async with mod.AsyncClient(transport=mod.MockTransport(handler)) as client:
            responses=[await client.post('https://api.deepseek.com/chat/completions',json={'messages':[]},headers={'Authorization':'Bearer test-secret','Cookie':'cookie-secret'}) for _ in range(2)]
        assert [r.status_code for r in responses]==[503,200]
        assert [rows[i]['status'] for i in (1,2)]==[503,200]
        assert all(r['complete'] and r['transport']==module for r in rows.values())
        assert rows[2]['response']==responses[1].json()
        assert not any(x in json.dumps(rows) for x in ('test-secret','response-secret','cookie-secret'))
    finally: capture.uninstall()

@pytest.mark.asyncio
@pytest.mark.parametrize('module',['httpx','httpx2'])
async def test_stream_is_consumed_once_with_complete_usage(module):
    mod=importlib.import_module(module); rows={}
    chunks=[b'data: {"text":"hello"}\n\n',b'data: {"usage":{"prompt_tokens":4,"completion_tokens":1}}\n\n',b'data: [DONE]\n\n']
    class Stream(mod.AsyncByteStream):
        async def __aiter__(self):
            for x in chunks: yield x
    capture=ProviderCapture(lambda r:rows.update({r['index']:dict(r)}));capture.install()
    try:
        async with mod.AsyncClient(transport=mod.MockTransport(lambda r:mod.Response(200,stream=Stream()))) as client:
            async with client.stream('POST','https://api.deepseek.com/chat/completions',json={'stream':True}) as response:
                raw=b''.join([x async for x in response.aiter_bytes()])
        assert raw==b''.join(chunks)
        assert rows[1]['raw_response'].encode()==raw and rows[1]['complete']
        assert rows[1]['stream_events'][-1]['usage']['prompt_tokens']==4
    finally: capture.uninstall()

@pytest.mark.asyncio
@pytest.mark.parametrize('module',['httpx','httpx2'])
async def test_auth_failure_stops_fanout(module):
    mod=importlib.import_module(module);rows={}
    capture=ProviderCapture(lambda r:rows.update({r['index']:dict(r)}));capture.install()
    try:
        async with mod.AsyncClient(transport=mod.MockTransport(lambda r:mod.Response(401,json={'error':'invalid'}))) as client:
            assert (await client.post('https://api.deepseek.com/chat/completions',json={})).status_code==401
            with pytest.raises(RuntimeError,match='stopped'): await client.post('https://api.deepseek.com/chat/completions',json={})
        assert capture.count==1 and rows[1]['complete']
    finally: capture.uninstall()

@pytest.mark.asyncio
@pytest.mark.parametrize('module',['httpx','httpx2'])
async def test_network_error_is_recorded_without_exception_secrets(module):
    mod=importlib.import_module(module);rows={}
    def handler(r): raise mod.ConnectError('sensitive-detail',request=r)
    capture=ProviderCapture(lambda r:rows.update({r['index']:dict(r)}));capture.install()
    try:
        async with mod.AsyncClient(transport=mod.MockTransport(handler)) as client:
            with pytest.raises(mod.ConnectError): await client.post('https://api.deepseek.com/chat/completions',json={})
        assert rows[1]['error_type']=='ConnectError' and not rows[1]['complete']
        assert 'sensitive-detail' not in json.dumps(rows)
    finally: capture.uninstall()
