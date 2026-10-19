"""Real SQL/worker persistence and HTTP context, with scripted model outputs."""
import asyncio,json
from datetime import datetime,timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from test_round54_narrative_sql import setup_db
import test_round54_narrative_sql as nutrition_sql
from test_round56_evidence_sql import seed
from test_round56_evidence_completion import resolution
from test_round57_read_delivery import scripted_http
from test_round72_query_scope import DATA
from app.config import settings
from app.main import app
from app.database import get_db
from app.models.agent import AgentRun,AgentMessage,AgentToolCall,AgentProposal
from app.models.workout import WorkoutSession
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize('id',['CW12-r1','CW14-r1','CW15-r1','CW17-r1','CW19-r2'])
@pytest.mark.parametrize('total',[0,25])
async def test_worker_exact_status_multi_goal_and_persisted_reply(db_session,session_factory,monkeypatch,id,total):
    user,conv=await seed(db_session,total)
    before=(await db_session.execute(select(WorkoutSession.id,WorkoutSession.status,WorkoutSession.trained_at).order_by(WorkoutSession.id))).all()
    await db_session.commit()
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    query=DATA[id]['message'];r=resolution(query)
    if id=='CW15-r1':r.intent_domain='workout_history';r.resolved_query='核对本周未完成训练场次和计划完成日'
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    bad='已完成99场；计划完成7天是因为周一已经到期。'
    requests=scripted_http(monkeypatch,[{'role':'assistant','content':bad}])
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':query,'client_request_id':uuid4().hex});assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit();assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),20)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json();assert body['status']=='completed' and body['error_code'] is None,body
            trace=body['execution_trace'];receipt=trace.get('training_delivery');assert receipt,body
            assert receipt['history_session_count']==total and receipt['plan_completed_days']==0
            assert f'历史已完成{total}场' in body['reply'] and bad not in body['reply']
            assert '不要求关联当前计划' in body['reply']
            if id in {'CW12-r1','CW14-r1'}:assert '强度是否合适' in body['reply'] and '实际动作' in body['reply']
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
        assert len(requests)==1 and not requests[0].get('tools')
        views=[json.loads(m['content']) for m in requests[0]['messages'] if m['role']=='tool']
        proofs=[v['boundaries']['count_coverage'] for v in views if v['tool'].startswith('workout.')]
        assert all(v['queried_statuses']==['completed'] for v in proofs)
        assert all(v['exact_status_counts']=={'completed':total} for v in proofs)
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==(3 if total>20 else 2) and all(a.status=='completed' for a in audits)
            assert set(receipt['source_call_ids'])<={a.call_id for a in audits}
            assert (await db.scalar(select(AgentMessage).where(AgentMessage.run_id==rid,AgentMessage.role=='assistant'))).content==body['reply']
            assert (await db.execute(select(WorkoutSession.id,WorkoutSession.status,WorkoutSession.trained_at).order_by(WorkoutSession.id))).all()==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
    finally:app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario,count',[('normal',1),('sql_timeout_once',2),('provider_failure',1),('sql_error',1)])
async def test_meal_scope_proof_uses_actual_isolated_sql_and_http(db_session,session_factory,monkeypatch,scenario,count):
    original=nutrition_sql.provider;sent=[]
    def capture(monkeypatch,failure):
        requests=original(monkeypatch,failure);sent.append(requests);return requests
    monkeypatch.setattr(nutrition_sql,'provider',capture)
    await nutrition_sql.test_scoped_food_evidence_survives_handoff_and_failure(db_session,session_factory,monkeypatch,scenario,'narrative',count)
    for request in sent[0]:
        view=json.loads([m for m in request['messages'] if m['role']=='tool'][-1]['content'])
        proof=view['boundaries']['date_coverage']
        assert proof['queried_dates']==['2026-10-08'] and not proof['queried_empty_dates']
        assert proof['recorded_dates'][0]['observed_meal_types']==['午餐']
        assert proof['recorded_dates'][0]['other_meals_record_presence']=='not_queried'
        assert '隔离' not in json.dumps(proof,ensure_ascii=False)
        assert 'date_coverage' not in view['business_data']
