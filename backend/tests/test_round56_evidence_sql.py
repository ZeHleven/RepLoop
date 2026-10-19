"""Real ASGI/worker/SQL with an offline HTTP provider, including >20 sessions."""
import asyncio,importlib,json
from datetime import date,datetime,timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
import pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.agent import AgentRun,AgentToolCall,AgentProposal
from app.models.workout import WorkoutPlan,WorkoutSession,PlannedExercise
from app.models.exercise import Exercise
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from app.services.agent_tools import build_read_tools,WorkoutProgressArguments
from app.services.business_clock import BUSINESS_DATE
from test_agent_domain_proposals import _context
from test_round56_evidence_completion import row,resolution

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

async def seed(db,total):
    user,profile,conv,_=await _context(db,'r56-'+uuid4().hex)
    foreign,_,_,_=await _context(db,'r56-foreign-'+uuid4().hex)
    ex=Exercise(id='ex-'+uuid4().hex,name_zh='合成动作',name_en='Synthetic',category='strength',difficulty='beginner',muscle_primary=['chest'])
    db.add(ex);plan=WorkoutPlan(user_id=user.id,name='Synthetic count',days_per_week=1);db.add(plan);await db.flush()
    db.add(PlannedExercise(plan_id=plan.id,exercise_id=ex.id,day_of_week=1,sets=3,reps='10'))
    records=[]
    for owner,day,state,count in [(user.id,'2026-10-07','completed',total),(user.id,'2026-10-08','ended_early',2),(user.id,'2026-10-09','in_progress',1),(user.id,'2026-10-11','completed',3),(user.id,'2026-10-04','completed',4),(foreign.id,'2026-10-07','completed',9)]:
        for _ in range(count):records.append(WorkoutSession(user_id=owner,plan_id=plan.id if owner==user.id else None,plan_name='Synthetic count',trained_at=date.fromisoformat(day),status=state,started_at=datetime.fromisoformat(day+'T08:00:00+00:00'),completed_at=None if state=='in_progress' else datetime.fromisoformat(day+'T09:00:00+00:00')))
    db.add_all(records);await db.commit();return user,conv

def provider(monkeypatch,fail):
    requests=[]
    for module in ('httpx','httpx2'):
        mod=importlib.import_module(module);original=mod.AsyncClient.send
        async def send(client,request,_mod=mod,_original=original,**kwargs):
            if request.url.host=='test':return await _original(client,request,**kwargs)
            assert request.url.host=='api.deepseek.com'
            body=json.loads(request.content);requests.append(body)
            assert body['thinking']=={'type':'disabled'}
            assert not body.get('tools'), 'All required count evidence should already be collected'
            if fail:return _mod.Response(400,json={'error':{'message':'offline failure','type':'invalid_request_error'}},request=request)
            observed=[json.loads(m['content']).get('business_data') for m in body['messages'] if m['role']=='tool']
            history=next(d for d in observed if d and 'sessions' in d)
            aggregate=next((d for d in observed if d and d.get('aggregation')=='all_matching_sessions'),None)
            total=aggregate['total_sessions'] if aggregate else history['count']
            assert aggregate or history['truncated'] is False
            answer=f'2026-10-05至2026-10-09，历史已完成{total}场，计划关联完成0日；未关联不代表没练，不据此判断强度。'
            return _mod.Response(200,json={'id':'offline-r56','object':'chat.completion','model':'deepseek-flash','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':answer}}],'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}},request=request)
        monkeypatch.setattr(mod.AsyncClient,'send',send)
    return requests

@pytest.mark.asyncio
@pytest.mark.parametrize('total',[7,25])
@pytest.mark.parametrize('failure',[False,True])
async def test_actual_worker_preserves_complete_or_recovered_count_and_failure(db_session,session_factory,monkeypatch,total,failure):
    user,conv=await seed(db_session,total)
    async def snapshot(db):return (await db.execute(select(WorkoutSession.id,WorkoutSession.user_id,WorkoutSession.trained_at,WorkoutSession.status).order_by(WorkoutSession.id))).all()
    before=await snapshot(db_session)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline');monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(settings,'AGENT_PLANNED_EXECUTION_ENABLED',False)
    message=row('CW03-r1')['message'];r=resolution(message)
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    requests=provider(monkeypatch,failure);build=runtime.build_read_tools;actual=[]
    def builder(db,**kwargs):
        tools=build(db,**kwargs)
        for tool in tools:
            invoke=tool.coroutine
            async def tracked(_invoke=invoke,_name=tool.name,**args):
                result=await _invoke(**args);actual.append((_name,args,result));return result
            tool.coroutine=tracked
        return tools
    monkeypatch.setattr(runtime,'build_read_tools',builder)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex});assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit();assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json();assert body['status']==('failed' if failure else 'completed'),body
            trace=body['execution_trace'];assert len(trace['observations'])==(2 if total==7 else 3)
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
            if not failure:
                assert f'已完成{total}场' in body['reply'] and '计划关联完成0日' in body['reply']
                assert trace['evidence_contract']['status']=='complete'
                assert len(trace['evidence_contract']['substitutions'])==int(total==7)
        assert len(requests)==1
        hist=next(x for x in actual if x[0]=='workout_list_history');assert hist[2]['count']==min(20,total) and hist[2]['total_count']==total
        assert hist[2]['truncated']==(total>20) and hist[2]['status_filter']==['completed']
        assert all(s['trained_at']=='2026-10-07' for s in hist[2]['sessions'])
        if total>20:
            agg=next(x for x in actual if x[0]=='workout_get_progress');assert agg[2]['total_sessions']==25 and agg[2]['count_scope']['statuses']==['completed']
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all();assert len(audits)==len(actual)
            for audit in audits:
                observed=next(o for o in trace['observations'] if o['call_id']==audit.call_id)
                assert observed['summary']==audit.result_data and observed['status']=='success'
                action=next(x for x in trace['actions'] if x['call_id']==audit.call_id);assert action['arguments']==audit.arguments_data
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
    finally:app.dependency_overrides.clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('statuses,expected',[(['completed'],25),(['ended_early'],2),(['completed','ended_early'],27),(['skipped'],0)])
async def test_exact_aggregate_is_scoped_unlimited_and_owner_isolated(db_session,statuses,expected):
    user,_=await seed(db_session,25);token=BUSINESS_DATE.set(date(2026,10,9))
    try:
        tool=build_read_tools(db_session,user_id=user.id,allowlist=['workout.get_progress'])[0]
        data=await tool.ainvoke({'start_date':'2026-10-05','end_date':'2026-10-11','statuses':statuses})
        assert data['total_sessions']==expected and data['aggregation']=='all_matching_sessions'
        assert data['count_scope']=={'start_date':'2026-10-05','end_date':'2026-10-09','requested_end_date':'2026-10-11','as_of':'2026-10-09','timezone':'Asia/Shanghai','statuses':statuses}
        old=await tool.ainvoke({'weeks':1});assert old['total_sessions']==27 and 'count_scope' not in old
    finally:BUSINESS_DATE.reset(token)
