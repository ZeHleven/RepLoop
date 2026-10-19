"""Real queued worker, scoped SQL reads, recorded proof and wire persistence."""
import asyncio,json
from datetime import date,datetime,timezone
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.database import get_db
from app.config import settings
from app.models.agent import AgentRun,AgentToolCall,AgentProposal
from app.models.meal import MealLog,MealItem
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_nutrition_scope import nutrition_read_evidence
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from app.services.business_clock import BUSINESS_DATE
from test_agent_domain_proposals import _context
from test_round49_required_evidence import resolution
from test_round57_read_delivery import scripted_http

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['recent','repair','missing_date','history_only','today_empty'])
async def test_equivalent_source_survives_final_guard_and_sql_persistence(db_session,session_factory,monkeypatch,mode):
    owner,_,conv,_=await _context(db_session,'r69-'+uuid4().hex)
    foreign,_,_,_=await _context(db_session,'r69-foreign-'+uuid4().hex)
    for uid,day,name,protein in [(owner.id,'2026-10-07' if mode=='missing_date' else '2026-10-08','本人的食物',12.0),(foreign.id,'2026-10-08','其他用户的食物',888.0)]:
        meal=MealLog(user_id=uid,logged_at=date.fromisoformat(day),meal_type='午餐');db_session.add(meal);await db_session.flush()
        db_session.add(MealItem(meal_id=meal.id,food_name=name,amount_g=100,calories=100,protein_g=protein,carbs_g=10,fat_g=1))
    await db_session.commit()
    async def snapshot(db):
        return (await db.execute(select(MealItem.id,MealItem.food_name,MealItem.protein_g).order_by(MealItem.id))).all()
    before=await snapshot(db_session)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for key,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,key,value)
    message='评估今天午餐的记录，给一条建议。' if mode=='today_empty' else '评估昨天午餐的记录，给一条建议。'
    token=BUSINESS_DATE.set(date(2026,10,9))
    try:
        hints=['nutrition_history','nutrition_today'] if mode=='history_only' else ['nutrition_history','nutrition_recent_context','profile_summary']
        evidence,optional,quotes=nutrition_read_evidence(message,domain='nutrition',kind='assessment',effect='read',risk='low',evidence=hints)
    finally:BUSINESS_DATE.reset(token)
    r=resolution(resolved_query=message,evidence_requirements=evidence,optional_evidence_requirements=optional,read_scope_quotes=quotes)
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    tool='nutrition_list_history' if mode=='history_only' else 'nutrition_get_recent_context'
    args={'start_date':'2026-10-08','end_date':'2026-10-08'} if mode=='history_only' else {}
    call={'role':'assistant','content':'','tool_calls':[{'id':'r69-read','type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]}
    answer='这里只核对已保存记录，全天实际摄入完整性未知。一般建议：核实是否有已吃但未记录的食物。'
    # Today is collected by the required no-argument tool before generation.
    sequence=[] if mode=='today_empty' else [call]
    sequence.append({'role':'assistant','content':'蛋白质已经足够，不需要再加。' if mode=='repair' else answer})
    if mode=='repair':sequence.append({'role':'assistant','content':answer})
    requests=scripted_http(monkeypatch,sequence)
    actual=runtime.build_read_tools;raw=[]
    def capture(db,**kwargs):
        tools=actual(db,**kwargs)
        for t in tools:
            original=t.coroutine
            async def invoke(_f=original,_name=t.name,**args):
                result=await _f(**args);raw.append((_name,result));return result
            t.coroutine=invoke
        return tools
    monkeypatch.setattr(runtime,'build_read_tools',capture)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(owner.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
            assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit()
                assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),20)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json();trace=body['execution_trace']
            assert body['status']=='completed',body
            contract=trace['evidence_contract'];assert contract['status']==('partial' if mode=='missing_date' else 'complete')
            assert len(contract['substitutions'])==int(mode in {'recent','repair'})
            if mode=='missing_date':assert body['reply']!=answer and trace['termination_reason']=='required_evidence_missing'
            else:assert body['reply']==answer
            if mode=='repair':assert trace['assessment_delivery']['status']=='corrected' and not requests[-1].get('tools')
            if mode=='today_empty':
                data=next(d for name,d in raw if name=='nutrition_get_today');assert data['meals']==[] and data['total_calories']==0
                assert data['evidence_limits']['unlogged_intake_is_zero'] is False
            for name,data in raw:
                if name in {'nutrition_get_recent_context','nutrition_list_history'}:assert '其他用户的食物' not in json.dumps(data,ensure_ascii=False)
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
            messages=(await client.get('/api/v1/agent/conversations/'+conv.id+'/messages')).json()
            assert next(m['content'] for m in messages if m.get('run_id')==rid and m['role']=='assistant')==body['reply']
        async with session_factory() as db:
            saved=await db.get(AgentRun,rid);assert saved.execution_trace['evidence_contract']==contract
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==len(raw) and all(x.status=='completed' for x in audits)
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==owner.id))==0
            assert saved.input_tokens==12*len(requests) and saved.output_tokens==5*len(requests)
    finally:app.dependency_overrides.clear()
