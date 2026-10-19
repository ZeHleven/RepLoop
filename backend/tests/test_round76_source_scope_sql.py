"""Full multi-turn API/worker/SQL with scripted route and query-plan HTTP."""
import asyncio,copy,json,os
from pathlib import Path
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
from app.models.exercise import Exercise
from app.services import agent_runtime as runtime
from app.services.agent_intent import IntentResolverOutcome,normalize_resolution
from app.services.agent_jobs import claim_next_agent_run,process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context
from test_round56_evidence_sql import seed
from test_round57_read_delivery import scripted_http
from test_round76_live_evidence import ROWS,resolution,GOOD
from round76_core_support import CORE

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

def function_message(name,payload,id):
    return {'role':'assistant','content':'','tool_calls':[{'id':id,'type':'function','function':{'name':name,'arguments':json.dumps(payload,ensure_ascii=False)}}]}

async def run(client,session_factory,conv,query):
    posted=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':query,'client_request_id':uuid4().hex});assert posted.status_code==202,posted.text
    rid=posted.json()['run_id']
    async with session_factory() as db:
        item=await db.get(AgentRun,rid);item.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit()
        assert await claim_next_agent_run(db)==rid
    await asyncio.wait_for(process_agent_run(session_factory,rid),20)
    return (await client.get('/api/v1/agent/runs/'+rid)).json()

def install(monkeypatch,session_factory):
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    for k,v in [('DEEPSEEK_API_KEY','synthetic-offline'),('DEEPSEEK_BASE_URL','https://api.deepseek.com'),('AGENT_ENABLED',True),('AGENT_PLANNED_EXECUTION_ENABLED',False)]:monkeypatch.setattr(settings,k,v)

def query_plan(**changes):
    return dict(supported=True,response_mode='structured',days=30,start_date='',end_date='',logged_dates=[],meal_types=[],metrics=['calories'],operations=['list'],comparison_targets=[],comparison_scope='meal',comparison_scope_quote='')|changes

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['QQ01-r2','HN06-r1','HN11-r1'])
async def test_actual_saved_query_context_preserves_scope_and_completes_all_turns(db_session,session_factory,monkeypatch,id):
    user,_,conv,_=await _context(db_session,'r76-'+uuid4().hex)
    other,_,_,_=await _context(db_session,'r76-other-'+uuid4().hex)
    days=['2026-08-30','2026-09-27','2026-10-02','2026-10-04','2026-10-05','2026-10-07','2026-10-08','2026-10-09','2026-10-11']
    for uid in (user.id,other.id):
        for day in days:
            for i,meal_type in enumerate(('早餐','午餐','晚餐'),1):
                meal=MealLog(user_id=uid,logged_at=date.fromisoformat(day),meal_type=meal_type);db_session.add(meal);await db_session.flush()
                db_session.add(MealItem(meal_id=meal.id,food_name='本人食物' if uid==user.id else '其他用户食物',amount_g=100,calories=100*i if uid==user.id else 9999,protein_g=10,carbs_g=20,fat_g=3))
    await db_session.commit()
    async def snapshot(db):return (await db.execute(select(MealItem.id,MealItem.calories,MealItem.food_name).order_by(MealItem.id))).all()
    before=await snapshot(db_session);install(monkeypatch,session_factory)
    logged=['2026-08-30','2026-09-30','2026-10-03','2026-10-05','2026-10-08','2026-10-11']
    plans=([query_plan(logged_dates=logged,metrics=['calories','protein_g','carbs_g','fat_g'],operations=['list','sum','mean'])] if id.startswith('QQ') else [
        query_plan(days=2),query_plan(days=4),query_plan(start_date='2026-10-02',end_date='2026-10-09',meal_types=['午餐']),
        query_plan(start_date='2026-10-02',end_date='2026-10-09',meal_types=['晚餐']),query_plan(start_date='2026-09-27',end_date='2026-10-09',meal_types=['晚餐']),
        query_plan(logged_dates=['2026-10-04','2026-10-05'],meal_types=['晚餐'],operations=['list','sum'])] if id.startswith('HN06') else [
        query_plan(days=3),query_plan(logged_dates=['2026-09-27','2026-10-02','2026-10-04']),query_plan(logged_dates=['2026-09-27','2026-10-02','2026-10-04'],meal_types=['晚餐']),
        query_plan(logged_dates=['2026-10-02','2026-10-04'],meal_types=['晚餐']),query_plan(start_date='2026-10-07',end_date='2026-10-09',meal_types=['早餐']),query_plan(days=3)])
    sequence=[];requests=scripted_http(monkeypatch,sequence);last=None;usage_rows=[]
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            for turn,plan in enumerate(plans):
                raw=next(p for p in CORE[id]['provider'] if p['turn']==turn)
                route=json.loads(raw['response']['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
                if last and route.get('task_update',{}).get('action') in {'continue','resume'}:
                    route['task_update']['task_id']=last['execution_trace']['task_state']['active_task_id']
                sequence.extend([function_message('submit_semantic_route',route,f'route-{turn}'),function_message('submit_nutrition_history_query',plan,f'query-{turn}')])
                body=await run(client,session_factory,conv,CORE[id]['turns'][turn]['message']);trace=body['execution_trace']
                assert body['error_code'] is None and body['status']=='completed',body
                assert trace['evidence_contract']['status']=='complete' and trace['evidence_contract']['required_tools']==['nutrition.list_history']
                assert len(requests)==2*(turn+1)
                usage_rows.append({'id':id,'turn':turn,'run_id':body['id'],'requests_total':len(requests),'input_tokens':body['input_tokens'],'output_tokens':body['output_tokens'],'budget':trace['budget_usage'],'expected_input':24,'expected_output':10,'provider_calls':2,'model_usage':body['model_usage']})
                assert body['model_usage']['http_requests']==2 and body['model_usage']['usage_known_requests']==2
                assert trace['budget_usage']['model_calls']==0, 'Structured query has no graph generation; provider metric is separate'
                async with session_factory() as db:
                    calls=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==body['id']))).all()
                    assert len(calls)==1 and calls[0].status=='completed' and calls[0].tool_name=='nutrition.list_history'
                    arguments=calls[0].arguments_data
                    for key in ('days','start_date','end_date','logged_dates','meal_types'):assert arguments.get(key,[] if key in {'logged_dates','meal_types'} else '')==plan[key],(key,arguments,plan)
                    # The audit table stores a compact count; the persisted
                    # response card contains the full query scope and rows.
                    result=next(c['data'] for c in body['cards'] if c['type']=='nutrition.list_history')
                    assert calls[0].result_data['count']==len(result['days'])
                    assert '其他用户食物' not in json.dumps(result,ensure_ascii=False)
                    assert result['scope']['as_of']=='2026-10-09'
                    assert all(d['total_calories']<=600 for d in result['days'])
                    assert (await db.get(AgentRun,body['id'])).execution_trace==trace
                    assert (await db.get(AgentRun,body['id'])).model_usage==body['model_usage']
                last=body
            if id.startswith('QQ'):
                assert '2026-10-11' in body['reply'] and '1800' in body['reply'] and '600' in body['reply'],body['reply']
            else:
                requirements=last['execution_trace']['task_state']['tasks'][0]['requirements']
                assert any(r['quote'] in CORE[id]['turns'][-1]['message'] for r in requirements)
        async with session_factory() as db:
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
        evidence_dir=os.environ.get('R76_EVIDENCE_DIR')
        if evidence_dir:
            (Path(evidence_dir)/('structured-usage-'+id+'-'+uuid4().hex+'.json')).write_text(json.dumps({'business_checks_complete':True,'model_source':'scripted HTTP; every response has usage 12/5','usage':usage_rows},ensure_ascii=False,indent=2),encoding='utf8')
        assert all(u['input_tokens']==u['expected_input'] and u['output_tokens']==u['expected_output'] for u in usage_rows),usage_rows
    finally:app.dependency_overrides.clear()

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['source','conflict','optional','required_missing'])
async def test_source_and_obligation_contracts_survive_final_delivery(db_session,session_factory,monkeypatch,mode):
    user,conv=await seed(db_session,0)
    ex=(await db_session.scalars(select(Exercise).where(Exercise.name_zh=='合成动作'))).one();ex.name_zh='深蹲';await db_session.commit()
    install(monkeypatch,session_factory)
    row=ROWS['SV08-r1'];query=row['turns'][0]['message']+('并查询下一练。' if mode=='required_missing' else '')
    r=normalize_resolution(query,resolution(row['turns'][0]['body']))
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    if mode=='required_missing':
        original=runtime.build_read_tools
        monkeypatch.setattr(runtime,'build_read_tools',lambda *a,**kw:[t for t in original(*a,**kw) if t.name!='workout_get_next'])
    answer='深蹲先停。\n**已保存的资料**\n- 计划处方来自工具。深蹲3组。' if mode in {'source','conflict'} else GOOD
    if mode=='conflict':answer+='\n**当前临时安排**\n深蹲2组。'
    sequence=[{'role':'assistant','content':answer}]+([{'role':'assistant','content':GOOD}] if mode=='conflict' else [])
    requests=scripted_http(monkeypatch,sequence)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            body=await run(client,session_factory,conv,query);trace=body['execution_trace']
            if mode=='required_missing':
                assert trace['evidence_contract']['status']!='complete' and body['reply']!=GOOD
                assert 'workout.get_next' in trace['evidence_contract']['missing_tools']
            else:
                assert body['error_code'] is None and trace['evidence_contract']['status']=='complete',body
                assert set(trace['evidence_contract']['required_tools'])=={'plan.get_active','health.get_screening_summary'}
                assert body['reply']==(GOOD if mode=='conflict' else answer)
                assert len(requests)==(2 if mode=='conflict' else 1)
                if mode=='conflict':assert trace['assessment_delivery']['checks']=='assertion_scope_v7' and trace['assessment_delivery']['status']=='corrected'
                else:assert not trace.get('assessment_delivery')
        async with session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
    finally:app.dependency_overrides.clear()
