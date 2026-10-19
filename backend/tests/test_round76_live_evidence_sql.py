"""Actual API/worker/SQL with dynamic tool calls and scripted provider HTTP."""
import asyncio,hashlib
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest,pytest_asyncio
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
from test_round57_read_delivery import scripted_http
from test_round76_live_evidence import ROWS,resolution,tool_calls,system,GOOD

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['normal','assessment','protocol','budget'])
async def test_dynamic_contract_in_persisted_worker_run(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,0)
    count=await db_session.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    if mode=='budget':monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',2)
    row=ROWS['SV01-r1'];query=row['turns'][0]['message'];r=resolution(row['turns'][0]['body'])
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    sequence=[tool_calls('plan_get_active','health_get_screening_summary'), tool_calls('plan_get_active',prefix='r76-budget-') if mode=='budget' else {'role':'assistant','content':
        '<｜DSML｜function_calls>bad</｜DSML｜function_calls>' if mode=='protocol' else '少练这一周不会有什么大损失。' if mode=='assessment' else GOOD}]
    if mode!='normal':sequence.append({'role':'assistant','content':GOOD})
    requests=scripted_http(monkeypatch,sequence)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            posted=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':query,'client_request_id':uuid4().hex});assert posted.status_code==202,posted.text
            rid=posted.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),20)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['error_code'] is None and body['reply']==GOOD,body
            assert len(requests)==len(sequence)
            assert 'current_symptom_report' not in system(requests[0])
            for req in requests[1:]:
                s=system(req);assert hashlib.sha256(query.encode()).hexdigest() in s
                assert 'r76-plan_get_active' in s and s.count('当前新症状与训练建议契约：')==1
            trace=body['execution_trace'];assert trace['budget_usage']['model_calls']==len(sequence)
            assert body['model_usage']['http_requests']==len(sequence)
            assert body['model_usage']['input_tokens']==12*len(sequence)
            assert body['model_usage']['output_tokens']==5*len(sequence)
        async with session_factory() as db:
            saved=await db.get(AgentRun,rid);assert saved.execution_trace==trace
            calls=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            completed=[c for c in calls if c.status=='completed'];assert len(completed)==2
            assert len(calls)==(3 if mode=='budget' else 2)
            if mode=='budget':assert any(c.error_code=='read_tool_budget_exhausted' and c.status=='failed' for c in calls)
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            assert await db.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))==count
            assert saved.input_tokens==12*len(sequence) and saved.output_tokens==5*len(sequence)
    finally:app.dependency_overrides.clear()
