"""Real SQL/API/worker delivery: wrong model text cannot change sourced facts."""
import asyncio,json
from datetime import datetime,date,timezone
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
from app.models.workout import WorkoutSession,WorkoutPlan,PlannedExercise
from app.models.profile import UserProfile,WeightLog
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome,IntentResolution
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from app.services.agent_tools import build_read_tools
from app.services.business_clock import BUSINESS_DATE
from test_round56_evidence_sql import seed
from test_round56_evidence_completion import resolution
from test_round57_read_delivery import scripted_http
from test_round58_grounded_delivery import row

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield


@pytest.mark.asyncio
@pytest.mark.parametrize('total',[0,25])
@pytest.mark.parametrize('weeks',[1,8,52])
async def test_real_calendar_progress_honors_range_despite_week_argument(db_session,monkeypatch,total,weeks):
    user,_=await seed(db_session,total);token=BUSINESS_DATE.set(date(2026,10,9))
    try:
        tool=build_read_tools(db_session,user_id=user.id,allowlist=['workout.get_progress'])[0]
        result=await tool.ainvoke({'weeks':weeks,'start_date':'2026-10-05','end_date':'2026-10-09','statuses':['completed']})
        assert result['aggregation']=='all_matching_sessions' and result['total_sessions']==total
        assert result['count_scope']['end_date']=='2026-10-09' and result['count_scope']['statuses']==['completed']
    finally:BUSINESS_DATE.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['zero','truncated','temporary','weight'])
async def test_worker_replaces_incorrect_prose_with_checked_delivery(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,25 if mode=='truncated' else 0)
    profile=await db_session.scalar(select(UserProfile).where(UserProfile.user_id==user.id))
    profile.injuries=['膝部不适']
    if mode=='weight':db_session.add(WeightLog(user_id=user.id,weight_kg=72,recorded_at=datetime(2026,10,8,tzinfo=timezone.utc)))
    await db_session.commit()
    async def snapshot(db):
        return ((await db.execute(select(WorkoutSession.id,WorkoutSession.user_id,WorkoutSession.status,WorkoutSession.trained_at).order_by(WorkoutSession.id))).all(),
                (await db.execute(select(PlannedExercise.id,PlannedExercise.day_of_week,PlannedExercise.sets,PlannedExercise.reps).order_by(PlannedExercise.id))).all(),
                (await db.execute(select(UserProfile.user_id,UserProfile.weight_kg,UserProfile.injuries).order_by(UserProfile.user_id))).all(),
                (await db.execute(select(WeightLog.id,WeightLog.weight_kg).order_by(WeightLog.id))).all())
    before=await snapshot(db_session)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    id={'zero':'CW13-r2','truncated':'CW08-r1','temporary':'WX15-r1','weight':'WX17-r2'}[mode]
    message=row(id)['message'];r=resolution(message,mode in {'temporary','weight'},'query','low')
    if mode=='weight':
        r.evidence_requirements=['active_plan','health_screening','weight_history']
        r.read_scope_quotes=[message]
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    bad='本周完成99次，计划应完成7天；每周3次各10组是减量，下一次直接回80kg，没有膝部限制。'
    seq=[]
    if mode=='weight':seq.append({'role':'assistant','content':'','tool_calls':[{'id':'source-weight','type':'function','function':{'name':'weight_list_history','arguments':json.dumps({'limit':30})}}]})
    seq.append({'role':'assistant','content':bad})
    requests=scripted_http(monkeypatch,seq)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex});assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit();assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json();assert body['status']=='completed' and body['error_code'] is None,body
            receipt=body['execution_trace'].get('training_delivery');assert receipt,body
            assert bad not in body['reply'] and '99次' not in body['reply']
            if mode in {'zero','truncated'}:
                assert receipt['history_session_count']==(25 if mode=='truncated' else 0) and receipt['plan_completed_days']==0
                assert '计划关联完成0日' in body['reply']
            else:
                assert receipt['temporary_days']==1 and receipt['temporary_weekly_sets'] < receipt['baseline_weekly_sets']
                assert '膝部不适' in body['reply'] and '余力' in body['reply']
                if mode=='weight':assert '72 kg' in body['reply'] and 'weight.list_history' in [x['tool_id'] for x in body['execution_trace']['actions']]
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            expected=3 if mode in {'truncated','weight'} else 2
            assert len(audits)==expected and all(x.status=='completed' for x in audits)
            assert set(receipt['source_call_ids']) <= {x.call_id for x in audits}
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            saved=await db.get(AgentRun,rid)
            assert saved.input_tokens==len(seq)*12 and saved.output_tokens==len(seq)*5
            assert len(requests)==len(seq)
    finally:app.dependency_overrides.clear()
