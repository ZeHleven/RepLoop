"""Real SQL/API/worker with recorded routes and an offline native provider."""
import asyncio,json
from datetime import datetime,timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
import pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from evals.tool_attempt_ledger import tool_attempt_ledger
from app.main import app
from app.database import get_db
from app.config import settings
from app.models.agent import AgentRun,AgentToolCall,AgentProposal
from app.models.profile import UserProfile
from app.models.workout import WorkoutSession,PlannedExercise
from app.services import agent_intent_model as routing
from app.services.agent_intent_model import IntentRouteDecision
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_round56_evidence_sql import seed
from test_round57_read_delivery import scripted_http
from test_round59_read_intent_ledger import archived,routes
from evals.tool_attempt_recorder import ToolRecorder,upsert_attempt,CONTEXT

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['counter','new_symptom','dispatch_rejection'])
async def test_read_request_and_attempt_evidence_persist_without_business_writes(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,1 if mode=='counter' else 0)
    profile=await db_session.scalar(select(UserProfile).where(UserProfile.user_id==user.id))
    profile.injuries=[];await db_session.commit()
    async def snapshot(db):
        return ((await db.execute(select(UserProfile.user_id,UserProfile.injuries,UserProfile.weight_kg).order_by(UserProfile.user_id))).all(),
                (await db.execute(select(WorkoutSession.id,WorkoutSession.status,WorkoutSession.trained_at).order_by(WorkoutSession.id))).all(),
                (await db.execute(select(PlannedExercise.id,PlannedExercise.sets,PlannedExercise.reps).order_by(PlannedExercise.id))).all())
    before=await snapshot(db_session)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,name,value)
    case=archived({'counter':'CW15-r1','new_symptom':'WX24-r1','dispatch_rejection':'CW10-r2'}[mode])
    message=case['turns'][0]['message'];raw=routes(case)[0]
    monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
    answer=('旧档案未记录伤病不代表今天没有症状。今天突然膝痛，先暂停诱发疼痛的训练，不照原深蹲负重练。'
            '疼痛从什么时候开始，是否有外伤、肿胀、发热或不能承重？明显加重或不能承重时及时就医。'
            '这一周可以暂休；15分钟是上限，不用补课。待症状和精力改善、必要评估后再从轻量渐进恢复，练后及次日不加重才继续。'
            '没有保存健康信息或修改计划。') if mode=='new_symptom' else '只读核对已取得，原始模型数值99不可信。'
    sequence=[]
    if mode=='dispatch_rejection':sequence.append({'role':'assistant','content':'','tool_calls':[{'id':'rejected-native','type':'function','function':{'name':'workout_list_history','arguments':json.dumps({'limit':20,'start_date':'2026-10-05','end_date':'2026-10-09'})}}]})
    sequence.append({'role':'assistant','content':answer});requests=scripted_http(monkeypatch,sequence)
    attempts=[];recorder=ToolRecorder(lambda r:upsert_attempt(attempts,r));token=CONTEXT.set({'turn':0});recorder.install()
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex});assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit();assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['status']=='completed' and body['error_code'] is None,body
            assert body['requested_effect']=='read' and not body['change_requests']
            if mode=='new_symptom':
                assert body['reply']==answer and body['execution_trace'].get('training_delivery') is None, {'reply':body['reply'],'receipt':body['execution_trace'].get('training_delivery'),'requests':len(requests),'trace':body['execution_trace']}
                assert {'plan.get_active','health.get_screening_summary'} <= {x['tool_id'] for x in body['execution_trace']['actions']}
                assert any('膝盖突然疼' in m.get('content','') for q in requests for m in q['messages'] if m['role']=='user')
            else:
                receipt=body['execution_trace'].get('training_delivery');assert receipt,body
                assert receipt['history_session_count']==int(mode=='counter') and receipt['plan_completed_days']==0
                assert not body['clarification_required'] and '99' not in body['reply']
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
        async with session_factory() as db:
            stored=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            audits=[{key:getattr(x,key) for key in ('id','run_id','call_id','tool_name','arguments_data','result_data','status','duration_ms')} for x in stored]
            record={'turns':[{'body':body}],'tool_attempts':attempts,'tool_audits':audits,'provider':[{'turn':0,'request':q,'response':{'choices':[{'message':sequence[i]}]}} for i,q in enumerate(requests)]}
            ledger=tool_attempt_ledger(record);assert ledger['complete'],ledger
            assert ledger['errors']==int(mode=='dispatch_rejection')
            assert ledger['attempts']==len(attempts)+int(mode=='dispatch_rejection')
            assert ledger['dispatcher_rejections']==int(mode=='dispatch_rejection')
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
    finally:recorder.uninstall();CONTEXT.reset(token);app.dependency_overrides.clear()
