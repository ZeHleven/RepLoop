"""Real SQL/API/worker continuation; scripted provider is never a business score."""
import asyncio,copy,json
from datetime import datetime,timezone,timedelta
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.database import get_db
from app.config import settings
from app.models.agent import AgentRun,AgentToolCall,AgentProposal
from app.models.profile import UserProfile
from app.models.workout import WorkoutPlan,PlannedExercise
from app.services import agent_intent_model as routing
from app.services.agent_intent_model import IntentRouteDecision
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_round56_evidence_sql import seed
from test_round60_symptom_budget_sql import provider
from test_round61_training_continuation import row

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('variant',['unchanged','updated','no_active_plan'])
async def test_clarification_continues_with_current_user_sql_evidence(db_session,session_factory,monkeypatch,variant):
    user,conv=await seed(db_session,0)
    profile=await db_session.scalar(select(UserProfile).where(UserProfile.user_id==user.id));profile.injuries=[]
    await db_session.commit()
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for k,v in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,k,v)
    r=row('WX35-r1');state={'index':0,'task':None};requests,providers=provider(monkeypatch,[{'role':'assistant','content':'离线首轮，请补充症状信息。'},{'role':'assistant','content':'离线续接执行检查，不计真实建议正确率。'}],False)
    async def route(*args,**kwargs):
        raw=copy.deepcopy(r['routes'][state['index']])
        if state['index']:raw['task_update']['task_id']=state['task']
        return IntentRouteDecision.model_validate(raw)
    monkeypatch.setattr(routing,'_invoke_model_route',route)
    monkeypatch.setattr(routing,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No external route')))
    async def snapshot(db):
        return ((await db.execute(select(WorkoutPlan.id,WorkoutPlan.user_id,WorkoutPlan.name,WorkoutPlan.is_active).order_by(WorkoutPlan.id))).all(),(await db.execute(select(PlannedExercise.id,PlannedExercise.sets).order_by(PlannedExercise.id))).all(),(await db.execute(select(UserProfile.user_id,UserProfile.injuries).order_by(UserProfile.user_id))).all())
    bodies=[]
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            for index,turn in enumerate(r['turns']):
                state['index']=index
                if index and variant!='unchanged':
                    async with session_factory() as db:
                        plan=await db.scalar(select(WorkoutPlan).where(WorkoutPlan.user_id==user.id));plan.name='R61 updated after clarification'
                        if variant=='no_active_plan':plan.is_active=False
                        health=await db.scalar(select(UserProfile).where(UserProfile.user_id==user.id));health.injuries=['R61 fresh screening']
                        await db.commit()
                async with session_factory() as db:before=await snapshot(db)
                response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':turn['message'],'client_request_id':uuid4().hex});assert response.status_code==202,response.text
                rid=response.json()['run_id']
                async with session_factory() as db:
                    run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc)+timedelta(minutes=index);await db.commit();assert await claim_next_agent_run(db)==rid
                await asyncio.wait_for(process_agent_run(session_factory,rid),15)
                body=(await client.get('/api/v1/agent/runs/'+rid)).json();bodies.append(body)
                assert body['status']=='completed' and body['error_code'] is None,body
                trace=body['execution_trace'];assert trace['evidence_contract']['status']=='complete'
                assert trace['termination_reason']!='required_evidence_missing'
                assert {a['tool_id'] for a in trace['actions']}=={'plan.get_active','health.get_screening_summary'}
                assert len(trace['observations'])==2
                if not index:state['task']=trace['task_state']['active_task_id']
                else:
                    assert trace['task_state']['transition']=='continue' and trace['task_state']['active_task_id']==state['task']
                    quotes=[v['quote'] for t in trace['task_state']['tasks'] if t['id']==state['task'] for v in t['requirements']]
                    assert any('12分钟' in q for q in quotes) and '不保存' in quotes
                async with session_factory() as db:
                    audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all();assert len(audits)==2
                    assert all(a.status=='completed' and a.arguments_data=={} for a in audits)
                    assert await snapshot(db)==before
                    assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
                assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
            evidence=[json.loads(m['content']) for m in requests[-1]['messages'] if m['role']=='tool']
            plan=next(v['business_data'] for v in evidence if v['tool']=='plan.get_active')
            health=next(v['business_data'] for v in evidence if v['tool']=='health.get_screening_summary')
            if variant=='updated':assert plan['plan']['name']=='R61 updated after clarification'
            elif variant=='no_active_plan':assert plan['found'] is False
            else:assert plan['plan']['user_id']==user.id
            assert health['injuries']==(['R61 fresh screening'] if variant!='unchanged' else [])
            assert 'r56-foreign' not in json.dumps(evidence)
            assert bodies[0]['execution_trace']['observations'][0]['call_id']!=bodies[1]['execution_trace']['observations'][0]['call_id']
            assert len(requests)==2
    finally:app.dependency_overrides.clear()
