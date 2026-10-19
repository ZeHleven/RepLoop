"""Use a fresh schema: other suite tests intentionally retain queued Runs."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient,ASGITransport
import pytest_asyncio
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.database import get_db
from app.config import settings
from app.services.agent_intent import IntentResolution
from app.services import nutrition_history_reporting as reporting
from test_round35_query_contracts import VALID

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # Override the public-table truncation fixture; engine owns a fresh schema.
    yield

@pytest_asyncio.fixture
async def client(session_factory):
    async def get_session():
        async with session_factory() as db: yield db
    app.dependency_overrides[get_db]=get_session
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            yield client
    finally: app.dependency_overrides.clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('unsupported',[False,True])
async def test_failed_planning_persists_visible_reply_and_zero_reads(client,db_session,session_factory,monkeypatch,unsupported):
    from uuid import uuid4
    from sqlalchemy import select
    from app.models.agent import AgentMessage,AgentRun,AgentToolCall
    from app.services.agent_intent import IntentResolverOutcome
    from app.services import agent_runtime
    from app.services.agent_jobs import claim_next_agent_run,process_agent_run
    from app.services.auth import create_access_token
    from test_agent_domain_proposals import _context
    user,_,conversation,_=await _context(db_session,'r35-'+uuid4().hex)
    message='查最近3个有记录饮食日期的热量'
    resolution=IntentResolution(primary_intent='nutrition_history_query',intent_domain='nutrition',request_kind='query',requested_effect='read',evidence_requirements=['nutrition_history'],resolved_query=message,confidence=.99)
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic')
    monkeypatch.setattr(agent_runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=resolution,source='model',attempt_count=1)))
    payload={**VALID,'supported':False} if unsupported else {**VALID,'start_date':'null','end_date':'null'}
    model=AsyncMock(return_value=SimpleNamespace(payload=payload))
    monkeypatch.setattr(reporting,'structured_chat_completion',model)
    tool=AsyncMock(side_effect=AssertionError('No query before validated plan'))
    monkeypatch.setattr(agent_runtime,'build_read_tools',lambda *a,**kw:[SimpleNamespace(name='nutrition_list_history',ainvoke=tool)])
    headers={'Authorization':'Bearer '+create_access_token(user.id)}
    response=await client.post('/api/v1/agent/runs',json={'conversation_id':conversation.id,'message':message,'client_request_id':'r35-'+uuid4().hex},headers=headers)
    assert response.status_code==202,response.text
    rid=response.json()['run_id']
    async with session_factory() as db:
        claimed=await claim_next_agent_run(db)
    assert claimed==rid
    await process_agent_run(session_factory,rid)
    response=await client.get('/api/v1/agent/runs/'+rid,headers=headers)
    body=response.json()
    category='nutrition_query_unsupported' if unsupported else 'nutrition_query_plan_invalid'
    assert body['status']=='completed' and body['error_code']==category
    assert body['reply'] and '连接' not in body['reply'] and not body['cards']
    assert body['execution_trace']['terminal_action']=='safe_stop'
    assert body['execution_trace']['termination_reason']==category
    async with session_factory() as db:
        messages=(await db.scalars(select(AgentMessage).where(AgentMessage.run_id==rid,AgentMessage.role=='assistant'))).all()
        assert len(messages)==1 and messages[0].content==body['reply']
        assert not (await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
    assert model.await_count==2 and tool.await_count==0
