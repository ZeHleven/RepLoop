"""Continuation context through actual ASGI, worker and PostgreSQL; scripted model."""
import asyncio,hashlib,json
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
from app.models.exercise import Exercise
from app.services import agent_runtime as runtime
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
@pytest.mark.parametrize('mode',['continuation','false_pause','effect_repair'])
async def test_symptom_qualifiers_and_facts_survive_continuation_worker_and_repair(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,0)
    exercise=(await db_session.scalars(select(Exercise).where(Exercise.name_zh=='合成动作'))).one();exercise.name_zh='深蹲'
    await db_session.commit()
    count=await db_session.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    query='补充一下：今天搬东西后右手腕靠拇指侧疼，约2分，没有肿胀和麻木，拿杯子不疼，撑地和握重物才疼。仍然一周每次最多12分钟，不保存；请按已存计划给具体取舍及回归条件。'
    r=resolution(query,True,'query','medium')
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    good='仅用于上下文传递测试：使用你刚提供的诱发动作信息，12分钟是上限，日常功能改善后再恢复。不保存。'
    first='卧推暂停，深蹲保留。当前深蹲2组。' if mode=='false_pause' else '这一周少练不会有什么大损失。' if mode=='effect_repair' else good
    seq=[{'role':'assistant','content':first}]+([{'role':'assistant','content':good}] if mode=='effect_repair' else [])
    requests=scripted_http(monkeypatch,seq)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            res=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':query,'client_request_id':uuid4().hex});assert res.status_code==202,res.text
            rid=res.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json();assert body['error_code'] is None,body
            trace=body['execution_trace'];assert len(trace['actions'])==len(trace['observations'])==2
            n=2 if mode=='effect_repair' else 1
            assert len(requests)==n and trace['budget_usage']['model_calls']==n
            for request in requests:
                system='\n'.join(m['content'] for m in request['messages'] if m['role']=='system')
                assert query in system and '撑地和握重物才疼' in system and '拿杯子不疼' in system
                assert hashlib.sha256(query.encode()).hexdigest() in system
                assert 'curated_general_movement_reference_not_user_data' in system
                assert 'exercise-library/362/goblet-squat/' in system
            audit=trace.get('assessment_delivery')
            if mode=='effect_repair':assert audit['checks']=='assertion_scope_v7' and audit['status']=='corrected' and body['reply']==good
            else:assert audit is None and body['reply']==first
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()['execution_trace']==trace
        async with session_factory() as db:
            saved=await db.get(AgentRun,rid);assert saved.execution_trace==trace
            calls=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all();assert len(calls)==2 and all(x.status=='completed' for x in calls)
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            assert await db.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))==count
            assert saved.input_tokens==12*n and saved.output_tokens==5*n
    finally:app.dependency_overrides.clear()
