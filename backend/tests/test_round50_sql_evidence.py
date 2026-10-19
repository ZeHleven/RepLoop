"""Provider faults retain owned SQL audits; stale workers cannot persist them."""
import asyncio
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func,text
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.config import settings
from app.main import app
from app.database import get_db
from app.models.agent import AgentToolCall,AgentRun,AgentProposal
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context
from test_round49_required_evidence import resolution
from test_round50_provider_evidence import http_provider

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('fail_at,sql_timeout,count',[(1,False,2),(2,False,3),(2,True,4)])
async def test_worker_preserves_completed_reads_when_provider_fails(db_session,session_factory,monkeypatch,fail_at,sql_timeout,count):
    user,_,conv,_=await _context(db_session,'r50-'+uuid4().hex)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(settings,'AGENT_PLANNED_EXECUTION_ENABLED',False)
    r=resolution(resolved_query='评估2026-10-08的饮食记录')
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    requests=http_provider(monkeypatch,fail_at=fail_at)
    if sql_timeout:
        original=runtime.build_read_tools;invoked=[]
        def builder(db,**kwargs):
            tools=original(db,**kwargs)
            coroutine=tools[0].coroutine
            async def once(**args):
                invoked.append(1)
                if len(invoked)==1:
                    try:await db.execute(text('SELECT 1/0'))
                    except Exception as exc:raise TimeoutError() from exc
                return await coroutine(**args)
            tools[0].coroutine=once
            return tools
        monkeypatch.setattr(runtime,'build_read_tools',builder)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            res=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':r.resolved_query,'client_request_id':uuid4().hex})
            assert res.status_code==202,res.text
            rid=res.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['status']=='failed' and body['reply'] is None,body
            trace=body['execution_trace']
            assert trace['terminal_action']=='failed'
            assert trace['budget_usage']['tool_calls']==count
            assert len(trace['observations'])==count
            assert sum(x['status']=='error' for x in trace['observations'])==int(sql_timeout)
            assert trace['evidence_contract']['status']==('partial' if fail_at==1 else 'complete')
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()['execution_trace']==trace
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==count and all(x.duration_ms is not None for x in audits)
            assert sum(x.status=='failed' for x in audits)==int(sql_timeout)
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
        assert len(requests)==fail_at
    finally:app.dependency_overrides.clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('current_attempt',[1,2])
async def test_failure_audit_obeys_ownership_and_deduplicates(db_session,current_attempt):
    _,_,_,run=await _context(db_session,'r50-owned-'+uuid4().hex)
    rid=run.id;run.status='running';run.attempt_count=current_attempt;await db_session.commit()
    def audit():return AgentToolCall(run_id=rid,tool_name='profile.get_summary',call_id='prefetch-owned',arguments_data={},result_data={'found':True},status='completed',duration_ms=1)
    async def mark():await runtime._mark_owned_run_failed(db_session,run_id=rid,expected_attempt_count=1,error_code='synthetic',duration_ms=1,tool_records=[audit()])
    if current_attempt==1:await mark()
    with pytest.raises(runtime.AgentRunOwnershipLost):await mark()
    count=await db_session.scalar(select(func.count()).select_from(AgentToolCall).where(AgentToolCall.run_id==rid))
    current=await db_session.get(AgentRun,rid)
    assert count==(1 if current_attempt==1 else 0)
    assert current.status==('failed' if current_attempt==1 else 'running')
