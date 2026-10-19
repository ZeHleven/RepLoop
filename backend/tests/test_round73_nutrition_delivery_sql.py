"""Real PostgreSQL, durable worker and persisted HTTP final delivery; no model network."""
import asyncio,json
from datetime import date,datetime,timezone
from uuid import uuid4
from unittest.mock import AsyncMock
import pytest,pytest_asyncio
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
@pytest.mark.parametrize('mode',['assessment_range','target_declaration','assessment_missing','single','range','discrete','zero_advice','repair'])
async def test_source_delivery_end_to_end(db_session,session_factory,monkeypatch,mode):
    owner,_,conv,_=await _context(db_session,'r73-'+uuid4().hex)
    foreign,_,_,_=await _context(db_session,'r73-foreign-'+uuid4().hex)
    dates=['2026-10-07','2026-10-08','2026-10-09'] if mode=='assessment_range' else ['2026-10-06','2026-10-08']
    for uid,day,mealtype,name,protein in [(owner.id,d,'午餐','合成燕麦' if d==dates[0] else '合成豆腐',3.1 if d==dates[0] else 9.7) for d in dates]+[(owner.id,'2026-10-08','晚餐','排除的晚餐食物',42.0),(foreign.id,'2026-10-08','午餐','其他用户食物',888.0)]:
        meal=MealLog(user_id=uid,logged_at=date.fromisoformat(day),meal_type=mealtype);db_session.add(meal);await db_session.flush()
        db_session.add(MealItem(meal_id=meal.id,food_name=name,amount_g=100,calories=100,protein_g=protein,carbs_g=10,fat_g=1))
    await db_session.commit()
    async def snapshot(db):return (await db.execute(select(MealItem.id,MealItem.food_name,MealItem.protein_g).order_by(MealItem.id))).all()
    before=await snapshot(db_session)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for key,value in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,key,value)
    assessment=mode.startswith('assessment') or mode=='target_declaration'
    if mode=='assessment_range':message='评估10月7日至9日我的饮食，三大营养素有没有达到目标，给些具体调整建议。'
    elif mode=='target_declaration':message='昨天全部食物饮料都已记齐。请分别核对已有饮食记录与我的每日目标：蛋白质100克、碳水80克、脂肪20克，只给差额，别保存任何东西。'
    elif mode=='assessment_missing':message='评估10月6日至8日我的饮食，给一条建议。'
    else:
        scope='2026-10-06至2026-10-08' if mode=='range' else '2026-10-06和2026-10-08' if mode=='discrete' else '2026-10-08'
        message=f'核对{scope}午餐各食物的蛋白质，'+('不要搭配建议。' if mode=='zero_advice' else '给一条搭配建议。')
    token=BUSINESS_DATE.set(date(2026,10,9))
    try:
        hints=['nutrition_history','nutrition_recent_context','profile_summary'] if assessment else ['nutrition_history']
        evidence,optional,quotes=nutrition_read_evidence(message,domain='nutrition',kind='assessment',effect='read',risk='low',evidence=hints)
    finally:BUSINESS_DATE.reset(token)
    r=resolution(resolved_query=message,evidence_requirements=evidence,optional_evidence_requirements=optional,read_scope_quotes=quotes)
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    args=({} if assessment else {'start_date':'2026-10-06','end_date':'2026-10-08','meal_types':['午餐']} if mode=='range' else {'logged_dates':['2026-10-06','2026-10-08'] if mode=='discrete' else ['2026-10-08'],'meal_types':['午餐']})
    tool='nutrition_get_recent_context' if assessment else 'nutrition_list_history'
    call={'role':'assistant','content':'','tool_calls':[{'id':'r73-read','type':'function','function':{'name':tool,'arguments':json.dumps(args)}}]}
    answer='这是脚本模型草稿：建议加蔬菜、加蛋白，并补记。'
    sequence=[call,{'role':'assistant','content':'蛋白质已经足够，不需要再加。' if mode=='repair' else answer}]
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
            contract=trace['evidence_contract'];assert contract['status']==('partial' if mode=='assessment_missing' else 'complete')
            if assessment:
                assert len(contract['substitutions'])==int(mode!='assessment_missing')
                assert (body['reply']==answer)==(mode!='assessment_missing')
                assert 'nutrition_delivery' not in trace
            else:
                receipt=trace['nutrition_delivery'];assert receipt['max_actions']==int(mode!='zero_advice')
                assert len(receipt['actions'])==receipt['max_actions']
                assert body['reply']!=answer and '9.7g' in body['reply']
                assert '42g' not in body['reply'] and '其他用户' not in body['reply']
                assert all(f['meal_type']=='午餐' and f['protein_g'] in {3.1,9.7} for f in receipt['item_facts'])
                if mode=='range':assert receipt['empty_dates']==['2026-10-07'] and '不等于当天没有进食' in body['reply']
                if mode=='discrete':assert receipt['requested_dates']==['2026-10-06','2026-10-08'] and '2026-10-07' not in body['reply']
                if mode=='repair':
                    receipt=trace.get('assessment_delivery') or trace.get('nutrition_goal_delivery')
                    assert receipt['status']=='corrected' and receipt['repair_attempts']==1
                    assert receipt['draft']=='蛋白质已经足够，不需要再加。'
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
            messages=(await client.get('/api/v1/agent/conversations/'+conv.id+'/messages')).json()
            assert next(m['content'] for m in messages if m.get('run_id')==rid and m['role']=='assistant')==body['reply']
        async with session_factory() as db:
            saved=await db.get(AgentRun,rid);assert saved.execution_trace==trace
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid))).all()
            assert len(audits)==len(raw) and all(x.status=='completed' for x in audits)
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==owner.id))==0
            assert saved.input_tokens==12*len(requests) and saved.output_tokens==5*len(requests)
    finally:app.dependency_overrides.clear()
