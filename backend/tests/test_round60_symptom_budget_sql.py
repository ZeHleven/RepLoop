"""Real SQL/API/worker, explicit budget fault injection, no external calls."""
import asyncio,copy,importlib,json
from datetime import datetime,timezone
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from evals.tool_attempt_ledger import tool_attempt_ledger
from evals.tool_attempt_recorder import ToolRecorder,upsert_attempt,CONTEXT
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
from test_round60_symptom_budget import archived,budget_sequence
from test_round59_read_intent_ledger import routes

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

def provider(monkeypatch,sequence,fail_final):
    requests=[];rows=[]
    for module in ('httpx','httpx2'):
        mod=importlib.import_module(module);original=mod.AsyncClient.send
        async def send(client,request,_mod=mod,_original=original,**kwargs):
            if request.url.host=='test':return await _original(client,request,**kwargs)
            assert request.url.host=='api.deepseek.com'
            body=json.loads(request.content);requests.append(body)
            assert len(requests)<=len(sequence),'Unexpected extra generation'
            if fail_final and len(requests)==4:
                rows.append({'turn':0,'request':body,'response':{},'status':400})
                return _mod.Response(400,json={'error':{'message':'injected final-generation rejection','type':'invalid_request_error'}},request=request)
            message=copy.deepcopy(sequence[len(requests)-1])
            payload={'id':'offline-r60-'+str(len(requests)),'object':'chat.completion','model':'deepseek-flash','choices':[{'index':0,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop','message':message}],'usage':{'prompt_tokens':12,'completion_tokens':5,'total_tokens':17}}
            rows.append({'turn':0,'request':body,'response':payload,'status':200})
            return _mod.Response(200,json=payload,request=request)
        monkeypatch.setattr(mod.AsyncClient,'send',send)
    return requests,rows

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['budget_recovered','model_limit','repair_failure','new_elbow'])
async def test_actual_worker_retains_budget_decisions_and_business_state(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,0)
    profile=await db_session.scalar(select(UserProfile).where(UserProfile.user_id==user.id))
    profile.injuries=[] if mode=='new_elbow' else ['膝部疼痛'];await db_session.commit()
    async def snapshot(db):
        return ((await db.execute(select(UserProfile.user_id,UserProfile.injuries,UserProfile.weight_kg).order_by(UserProfile.user_id))).all(),
                (await db.execute(select(WorkoutSession.id,WorkoutSession.status,WorkoutSession.trained_at).order_by(WorkoutSession.id))).all(),
                (await db.execute(select(PlannedExercise.id,PlannedExercise.sets,PlannedExercise.reps).order_by(PlannedExercise.id))).all())
    before=await snapshot(db_session)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for name,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False),('AGENT_MAX_TOOL_CALLS',4),('AGENT_MAX_MODEL_CALLS',3 if mode=='model_limit' else 4)]:monkeypatch.setattr(settings,name,value)
    case=archived('WX26-r1' if mode=='new_elbow' else 'WX04-r2');message=case['turns'][0]['message'];raw=routes(case)[0]
    monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
    seq=budget_sequence()
    if mode=='new_elbow':seq=[{'role':'assistant','content':'今天肘痛是当前自述，旧档案无记录不代表没有症状。请补充诱因、严重程度及活动受限情况。先停止诱发疼痛的训练，必要时评估；15分钟是上限，暂休也可以。改善后再逐步回归，未保存。'}]
    requests,providers=provider(monkeypatch,seq,mode=='repair_failure')
    attempts=[];recorder=ToolRecorder(lambda r:upsert_attempt(attempts,r));token=CONTEXT.set({'turn':0});recorder.install()
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex});assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit();assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['status']==('failed' if mode=='repair_failure' else 'completed'),body
            assert body['requested_effect']=='read' and not body['change_requests']
            if mode in {'budget_recovered','new_elbow'}:
                assert body['error_code'] is None and body['execution_trace']['terminal_action']=='answer',body
                if mode=='new_elbow':
                    assert body['reply']==seq[-1]['content']
                    assert any('current_symptom_evidence' in m.get('content','') and '今天肘部开始疼' in m['content'] for q in requests for m in q['messages'] if m['role']=='system')
                else:assert '尚未取得查询结果' not in body['reply'] and len(requests)==4
            elif mode=='model_limit':
                assert body['error_code']=='read_tool_budget_exhausted' and len(requests)==3
                assert '已取得' in body['reply']
            else:
                assert len(requests)==4 and not requests[-1].get('tools')
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
        async with session_factory() as db:
            stored=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            audits=[{key:getattr(x,key) for key in ('id','run_id','call_id','tool_name','arguments_data','result_data','status','duration_ms')} for x in stored]
            record={'turns':[{'body':body}],'tool_attempts':attempts,'tool_audits':audits,'provider':providers}
            ledger=tool_attempt_ledger(record);assert ledger['complete'],ledger
            if mode=='new_elbow':assert ledger['attempts']==2 and ledger['errors']==0
            else:
                assert (ledger['attempts'],ledger['function_invocations'],ledger['errors'],ledger['budget_rejections'],ledger['dispatcher_rejections'])==(5,3,2,1,1),ledger
                assert ledger['error_rate']==.4
                denied=next(a for a in audits if a['call_id']=='budget');assert denied['result_data']['executed'] is False
                for change in ['missing_audit','missing_native','bad_limit','bad_count','bad_fingerprint','fake_execution']:
                    broken=copy.deepcopy(record)
                    ba=next(a for a in broken['tool_audits'] if a['call_id']=='budget')
                    if change=='missing_audit':broken['tool_audits'].remove(ba)
                    elif change=='missing_native':broken['provider'][2]['response']['choices'][0]['message']['tool_calls']=[]
                    elif change=='bad_limit':ba['result_data']['limit']=100
                    elif change=='bad_count':ba['result_data']['used_before']=0
                    elif change=='bad_fingerprint':next(o for o in broken['turns'][0]['body']['execution_trace']['observations'] if o['call_id']=='budget')['result_fingerprint']='fake'
                    else:ba['result_data']['executed']=True
                    invalid=tool_attempt_ledger(broken);assert not invalid['complete'] and invalid['error_rate'] is None,change
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
    finally:recorder.uninstall();CONTEXT.reset(token);app.dependency_overrides.clear()
