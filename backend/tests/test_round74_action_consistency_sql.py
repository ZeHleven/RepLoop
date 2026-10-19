"""Real ASGI/worker/SQL persistence; scripted provider, no network model calls."""
import asyncio,json
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
@pytest.mark.parametrize('mode',['conflict','effect','still_conflict','protocol','budget'])
async def test_current_action_conflict_is_rewritten_and_persisted_without_business_write(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,0)
    # This shared seed uses a generic label; the checker must bind an actual
    # observed name, so make the fixture's only exercise the draft's 深蹲.
    exercise=(await db_session.scalars(select(Exercise).where(Exercise.name_zh=='合成动作'))).one()
    exercise.name_zh='深蹲'
    await db_session.commit()
    count_before=await db_session.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    message='档案没有这条，但今天膝盖突然疼。工作很累，给一周每次最多15分钟临时训练建议，说明恢复条件；不保存。'
    r=resolution(message,True,'query','medium')
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    bad='深蹲先停。\n当前临时安排：深蹲2组。\n每次最多15分钟，尚需澄清症状。'
    if mode=='effect':bad='这一周少练一次不会掉什么。'
    good='当前先暂停深蹲，15分钟是上限，可以休息；请补充起因、程度及日常功能。等症状和日常功能改善后，再从深蹲1组逐步恢复，观察练后次日反应。不保存。'
    if mode=='budget':monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',1)
    second=bad if mode=='still_conflict' else '<tool_call>fake</tool_call>' if mode=='protocol' else good
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':bad},{'role':'assistant','content':second}])
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            res=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
            assert res.status_code==202,res.text
            rid=res.json()['run_id']
            async with session_factory() as db:assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            expected={'conflict':None,'effect':None,'still_conflict':'read_assessment_boundary_invalid','protocol':'read_response_protocol_invalid','budget':'read_model_budget_exhausted'}[mode]
            assert body['error_code']==expected,body
            trace=body['execution_trace'];audit=trace['assessment_delivery']
            assert audit['draft']==bad and audit['checks']=='assertion_scope_v7'
            assert audit['status']==('corrected' if mode in {'conflict','effect'} else 'blocked')
            assert {f['code'] for f in audit['findings']}==({'training_effect_guarantee'} if mode=='effect' else {'training_action_conflict'})
            assert len(audit['source_call_ids'])==2
            assert len(trace['actions'])==len(trace['observations'])==2
            assert trace['budget_usage']['model_calls']==(1 if mode=='budget' else 2)
            assert trace['terminal_action']==('answer' if expected is None else 'safe_stop')
            if expected is None:assert body['reply']==good
            if mode!='budget':
                assert not requests[1].get('tools')
                payload=json.loads(requests[1]['messages'][-1]['content'])
                assert bad not in payload['revision']['working_draft']
                assert any(x['code']==audit['findings'][0]['code'] for x in payload['revision']['requirements'])
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()['execution_trace']==trace
        async with session_factory() as db:
            saved=await db.get(AgentRun,rid)
            assert saved.execution_trace['assessment_delivery']==audit
            calls=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(calls)==2 and all(x.status=='completed' for x in calls)
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            assert await db.scalar(select(func.count()).select_from(WorkoutSession).where(WorkoutSession.user_id==user.id))==count_before
            n=1 if mode=='budget' else 2
            assert len(requests)==n and saved.input_tokens==12*n and saved.output_tokens==5*n
    finally:app.dependency_overrides.clear()
