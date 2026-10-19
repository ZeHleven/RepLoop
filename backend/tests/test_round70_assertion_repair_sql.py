"""Verify the actual HTTP repair input and its durable worker receipt."""
import asyncio,json
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
from app.services.agent_assessment_repair import repair_input_hash
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_round56_evidence_sql import seed
from test_round56_evidence_completion import resolution
from test_round57_read_delivery import scripted_http

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['repair','negated_quote','named_property_after_denial'])
async def test_exact_revision_payload_persists_without_business_writes(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,0)
    before=await db_session.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    message='请依据已有计划和膝部不适记录解释一周临时训练建议，每次最多10分钟，说明回归条件；只给建议。'
    r=resolution(message,True,'query','medium')
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    draft={'repair':'深蹲本身不直接用手腕承重。仅给建议，不保存。',
           'negated_quote':'具体动作我没法替你保证“不涉及肩或膝”。',
           'named_property_after_denial':'我没法保证一切，但卧推膝盖不受力。'}[mode]
    good='现有证据不足以保证动作的个人安全性。仅验证离线交付，不保存。'
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':draft},{'role':'assistant','content':good}])
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
            assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['error_code'] is None,body
            trace=body['execution_trace'];receipt=trace.get('assessment_delivery')
            n=1 if mode=='negated_quote' else 2
            assert len(requests)==trace['budget_usage']['model_calls']==n
            if n==1:
                assert receipt is None and body['reply']==draft
            else:
                assert receipt['draft']==draft and receipt['repair_draft']==good and receipt['status']=='corrected'
                payload=json.loads(requests[1]['messages'][-1]['content'])
                assert payload['revision']==receipt['repair_input']
                assert repair_input_hash(payload['revision'])==receipt['repair_input_sha256']
                assert draft not in json.dumps(requests[1],ensure_ascii=False)
                assert not requests[1].get('tools')
                assert {x['tool'] for x in payload['observations']}=={'plan.get_active','health.get_screening_summary'}
                assert message in json.dumps(requests[1],ensure_ascii=False)
                assert body['reply']==good
            assert len(trace['actions'])==len(trace['observations'])==2
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()['execution_trace']==trace
        async with session_factory() as db:
            saved=await db.get(AgentRun,rid)
            assert saved.execution_trace.get('assessment_delivery')==receipt
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==2 and all(x.status=='completed' for x in audits)
            assert saved.input_tokens==12*n and saved.output_tokens==5*n
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            assert await db.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))==before
    finally:app.dependency_overrides.clear()
