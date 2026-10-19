"""Real worker/SQL persistence of repaired, invalid and locally limited answers."""
import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
import pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.database import get_db
from app.config import settings
from app.models.agent import AgentToolCall,AgentProposal,AgentRun
from app.models.workout import WorkoutSession
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_round56_evidence_sql import seed
from test_round56_evidence_completion import resolution
from test_round57_read_delivery import scripted_http,row

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['recover','invalid','budget','graph'])
async def test_delivery_result_and_audits_survive_sql_worker(db_session,session_factory,monkeypatch,mode):
    from langgraph.errors import GraphRecursionError
    from types import SimpleNamespace
    user,conv=await seed(db_session,0)
    count_before=await db_session.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    message=row('WX10-r1')['message'];r=resolution(message,True,'query','medium')
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    bad=row('WX10-r1')['reply']
    if mode=='budget':monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',1)
    if mode=='graph':monkeypatch.setattr(runtime,'create_agent',lambda **kwargs:SimpleNamespace(ainvoke=AsyncMock(side_effect=GraphRecursionError('offline ceiling'))))
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':bad},{'role':'assistant','content':bad if mode=='invalid' else '已读取计划和健康，本答复仅用于离线交付测试。'}])
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            res=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
            assert res.status_code==202,res.text
            rid=res.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            expected={'recover':None,'invalid':'read_response_protocol_invalid','budget':'read_model_budget_exhausted','graph':'read_graph_step_limit'}[mode]
            assert body['error_code']==expected,body
            assert body['reply'] and 'DSML' not in body['reply']
            assert len(body['execution_trace']['observations'])==2
            assert len(body['execution_trace']['actions'])==2
            assert body['execution_trace']['evidence_contract']['status']=='complete'
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()['execution_trace']==body['execution_trace']
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==2 and all(x.status=='completed' for x in audits)
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            assert await db.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))==count_before
            saved=await db.get(AgentRun,rid)
            expected_calls={'recover':2,'invalid':2,'budget':1,'graph':0}[mode]
            assert len(requests)==expected_calls
            assert body['execution_trace']['budget_usage']['model_calls']==expected_calls
            assert (saved.input_tokens or 0)==12*expected_calls and (saved.output_tokens or 0)==5*expected_calls
    finally:app.dependency_overrides.clear()
