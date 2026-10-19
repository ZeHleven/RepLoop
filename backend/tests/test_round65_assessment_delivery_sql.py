"""Actual ASGI queue, worker, read tools and PostgreSQL audit persistence."""
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
from test_round57_read_delivery import scripted_http

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # This module uses per-test PostgreSQL schemas, not the suite's session DB.
    yield

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['corrected','still_bad','protocol','budget','write_call'])
async def test_assessment_check_runs_in_actual_worker_without_writes(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,0)
    count_before=await db_session.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    message='请依据已有计划和膝部不适记录解释一周临时训练建议，每次最多10分钟，说明回归条件；只给建议。'
    r=resolution(message,True,'query','medium')
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    bad='卧推本身不涉及膝部负荷，但我不能保证姿势安全。'
    good='已取得计划和健康筛查；这里不对动作的个人安全性作保证。本答复仅验证离线交付流程。'
    if mode=='budget':monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',1)
    second={'role':'assistant','content':bad if mode=='still_bad' else '<tool_call>fake</tool_call>' if mode=='protocol' else good}
    if mode=='write_call':second={'role':'assistant','content':'','tool_calls':[{'id':'forged-save','type':'function','function':{'name':'save_plan','arguments':'{}'}}]}
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':bad},second])
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            res=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
            assert res.status_code==202,res.text
            rid=res.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            expected={'corrected':None,'still_bad':'read_assessment_boundary_invalid','protocol':'read_response_protocol_invalid','budget':'read_model_budget_exhausted','write_call':'read_response_protocol_invalid'}[mode]
            assert body['error_code']==expected,body
            assert bad not in body['reply']
            trace=body['execution_trace'];audit=trace['assessment_delivery']
            assert audit['draft']==bad and audit['status']==('corrected' if mode=='corrected' else 'blocked')
            assert len(audit['source_call_ids'])==2
            assert len(trace['actions'])==len(trace['observations'])==2
            assert trace['budget_usage']['model_calls']==(1 if mode=='budget' else 2)
            assert trace['terminal_action']==('answer' if mode=='corrected' else 'safe_stop')
            if mode!='budget':assert not requests[1].get('tools')
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()['execution_trace']==trace
        async with session_factory() as db:
            saved=await db.get(AgentRun,rid)
            assert saved.execution_trace['assessment_delivery']==audit
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==2 and all(x.status=='completed' for x in audits)
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            assert await db.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))==count_before
            n=1 if mode=='budget' else 2
            assert len(requests)==n and saved.input_tokens==12*n and saved.output_tokens==5*n
    finally:app.dependency_overrides.clear()
