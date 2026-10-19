"""Actual API/worker/SQL; scripted model errors are corrected at final delivery."""
import asyncio,copy,json
from datetime import date,datetime,timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.database import get_db
from app.config import settings
from app.models.agent import AgentRun,AgentMessage,AgentToolCall,AgentProposal
from app.models.profile import UserProfile
from app.models.workout import WorkoutPlan,PlannedExercise,WorkoutSession
from app.services import agent_intent_model as routing
from app.services.agent_intent_model import IntentRouteDecision
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from app.services.business_clock import BUSINESS_DATE
from test_round56_evidence_sql import seed
from test_round60_symptom_budget_sql import provider
from test_round63_training_delivery import row

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('variant',['no_time','zero','short_partial','extra_goal','current_symptom'])
async def test_worker_persists_checked_or_unowned_delivery_without_business_write(db_session,session_factory,monkeypatch,variant):
    user,conv=await seed(db_session,0)
    profile=await db_session.scalar(select(UserProfile).where(UserProfile.user_id==user.id));profile.injuries=[];profile.chronic_conditions=[];profile.onboarding_completed=True
    plan=await db_session.scalar(select(WorkoutPlan).where(WorkoutPlan.user_id==user.id))
    ex=await db_session.scalar(select(PlannedExercise).where(PlannedExercise.plan_id==plan.id))
    ex.sets=1 if variant=='zero' else 3;ex.rest_seconds=180 if variant=='zero' else 30 if variant=='short_partial' else 90
    await db_session.commit()
    async def snapshot(db):
        values=[]
        for model in [UserProfile,WorkoutPlan,PlannedExercise,WorkoutSession]:
            values.append((await db.execute(select(*model.__table__.columns).order_by(model.id))).all())
        return values
    before=await snapshot(db_session)
    await db_session.commit()  # Release the read transaction in this test loop.
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    token=BUSINESS_DATE.set(date(2026,10,9))
    for k,v in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,k,v)
    r=row('WX23-r1' if variant=='zero' else 'WX21-r1' if variant=='short_partial' else 'WK02-r1')
    message=r['message']
    if variant=='extra_goal':message+='另外分析最近8周训练趋势。'
    if variant=='current_symptom':message+='我现在走路也膝盖痛。'
    monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(r['routes'][0])))
    monkeypatch.setattr(routing,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external route')))
    sequence=copy.deepcopy(r['model_messages'])
    marker='SQL回放额外目标或当前症状需继续处理，不计业务成功。'
    sequence[-1]={'role':'assistant','content':marker if variant in {'extra_goal','current_symptom'} else r['reply']}
    requests,providers=provider(monkeypatch,sequence,False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex});assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit();assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),20)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['status']=='completed' and body['error_code'] is None,body
            trace=body['execution_trace'];delivery=trace.get('training_delivery')
            assert trace['evidence_contract']['status']=='complete'
            if variant in {'extra_goal','current_symptom'}:
                assert delivery is None and marker in body['reply']
            else:
                assert delivery and delivery['version']==2
                assert delivery['temporary_weekly_sets']<delivery['baseline_weekly_sets']==(1 if variant=='zero' else 3)
                assert body['reply']!=sequence[-1]['content']
                if variant=='zero':assert delivery['temporary_weekly_sets']==delivery['temporary_days']==0 and '不安排原力量工作组' in body['reply']
                if variant=='no_time':assert delivery['session_cap_minutes'] is None and delivery['session_cap_source']=='unspecified'
                if variant=='short_partial':assert delivery['temporary_weekly_sets']==1 and delivery['session_cap_minutes']==6
                observations={o['call_id']:o['result_fingerprint'] for o in trace['observations']}
                assert all(observations[k]==fp for k,fp in zip(delivery['source_call_ids'],delivery['source_fingerprints']))
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
        async with session_factory() as db:
            stored=await db.scalar(select(AgentMessage).where(AgentMessage.run_id==rid,AgentMessage.role=='assistant'))
            assert stored.content==body['reply']
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==len(trace['actions']) and all(x.status=='completed' for x in audits)
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
        assert len(requests)==len(sequence)
    finally:
        app.dependency_overrides.clear();BUSINESS_DATE.reset(token)
