"""New boundaries survive Run failure, SQL persistence, API and legacy replay."""
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

from evals.postgres_test_fixtures import engine, session_factory, db_session
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.agent import AgentRun,AgentMessage
from app.services import agent_runtime as runtime
from app.services.ai_client import AIServiceError
from app.services.agent_model_usage import ObservedAsyncClient
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context


@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield


async def api_get(factory,user_id,run_id,monkeypatch):
    async def override():
        async with factory() as db:yield db
    monkeypatch.setattr(settings,'AGENT_ENABLED',True)
    app.dependency_overrides[get_db]=override
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user_id)}) as client:
            result=await client.get('/api/v1/agent/runs/'+str(run_id))
            assert result.status_code==200,result.text
            return result.json()
    finally:app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['local','transport','http503'])
async def test_failed_run_persists_distinct_boundaries_and_api_reads_them(db_session,session_factory,monkeypatch,mode):
    user,_,conv,_=await _context(db_session,'r77-'+uuid4().hex)
    user_id=user.id
    run=AgentRun(user_id=user.id,conversation_id=conv.id,status='running',attempt_count=1)
    db_session.add(run);await db_session.commit();rid=run.id
    seen=[]
    async def provider(request):
        seen.append(True)
        if mode=='transport':raise httpx.ConnectError('synthetic')
        return httpx.Response(503,json={'error':'busy'})
    async def hook(request):
        if mode=='local':raise ValueError('local refusal')
    async def history(*args,**kwargs):
        try:
            async with ObservedAsyncClient(source='structured',transport=httpx.MockTransport(provider),event_hooks={'request':[hook]}) as client:
                await client.post('https://synthetic.invalid/')
        except (ValueError,httpx.ConnectError):pass
        raise AIServiceError('synthetic terminal failure')
    monkeypatch.setattr(runtime,'_load_history',history)
    with pytest.raises(AIServiceError):
        await runtime.execute_agent_run(db_session,run=run,conversation=conv,user_message='synthetic',expected_attempt_count=1)
    async with session_factory() as db:
        saved=await db.get(AgentRun,rid);usage=saved.model_usage
        assert saved.status=='failed' and saved.execution_trace is None
        assert usage['version']=='transport_boundary_v2' and usage['client_attempts']==1
        assert usage['transport_attempts']==len(seen)==(mode!='local')
        assert usage['local_errors']==(mode=='local') and usage['transport_errors']==(mode=='transport')
        assert usage['http_responses']==usage['http_errors']==(mode=='http503')
        assert saved.input_tokens==(0 if mode=='local' else None)
    body=await api_get(session_factory,user_id,rid,monkeypatch)
    assert body['model_usage']==usage and body['input_tokens']==(0 if mode=='local' else None)


@pytest.mark.asyncio
async def test_historical_v1_row_replay_and_api_keep_old_counts(db_session,session_factory,monkeypatch):
    user,_,conv,_=await _context(db_session,'r77-legacy-'+uuid4().hex)
    old={'version':'provider_http_v1','scope':'worker_attempt','worker_attempt':1,'prior_attempts_unobserved':False,
         'observation_complete':True,'http_requests':6,'http_successes':2,'http_errors':2,'transport_errors':2,
         'pending_or_cancelled':0,'usage_known_requests':2,'known_input_tokens':20,'known_output_tokens':4,
         'input_tokens':None,'output_tokens':None,'calls':[]}
    run=AgentRun(user_id=user.id,conversation_id=conv.id,status='running',attempt_count=1,model_usage=old)
    db_session.add(run);await db_session.flush();rid=run.id
    db_session.add(AgentMessage(conversation_id=conv.id,run_id=rid,role='assistant',content='archived reply',content_data={}))
    await db_session.commit()
    async def forbid(*args,**kwargs):raise AssertionError('replay must not call model')
    monkeypatch.setattr(runtime,'_load_history',forbid)
    result=await runtime.execute_agent_run(db_session,run=run,conversation=conv,user_message='synthetic',expected_attempt_count=1)
    assert result.reply=='archived reply'
    body=await api_get(session_factory,user.id,rid,monkeypatch)
    assert body['model_usage']==old and body['input_tokens'] is None
    async with session_factory() as db:assert (await db.get(AgentRun,rid)).model_usage==old
