"""Original synthetic task through actual API, worker, tool and PostgreSQL.

Only route/query-plan HTTP responses are scripted. These are not live evals.
"""
import copy,json,os
from datetime import date,datetime,timezone
from pathlib import Path
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.models.agent import AgentRun,AgentToolCall,AgentProposal
from app.models.meal import MealLog,MealItem
from app.services.agent_tools import build_read_tools
from app.services.auth import create_access_token
from app.services.business_clock import request_day
from app.services.nutrition_history_reporting import render_history
from app.schemas.nutrition_history import DateComparison
from test_agent_domain_proposals import _context
from test_round57_read_delivery import scripted_http
from test_round76_source_scope_sql import install,run,function_message
from round79_support import DATA,MESSAGE,plan,comparison

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

async def seed(db,variant='original'):
    user,_,conv,_=await _context(db,'r79-'+uuid4().hex)
    other,_,_,_=await _context(db,'r79-other-'+uuid4().hex)
    foods={f['name']:f for f in DATA['case']['foods']}
    for uid in (user.id,other.id):
        for source in DATA['case']['meals']:
            if variant=='missing' and source['date']=='2026-09-27' and uid==user.id:continue
            row=MealLog(user_id=uid,logged_at=date.fromisoformat(source['date']),meal_type=source['meal_type']);db.add(row);await db.flush()
            for name,grams in source['items'].items():
                food=foods[name]
                values={m:(9999 if uid==other.id else (0 if variant=='zero' else round(food[m]*grams/100,1))) for m in ('calories','protein_g','carbs_g','fat_g')}
                db.add(MealItem(meal_id=row.id,food_name=name if uid==user.id else '其他用户食物',amount_g=grams,**values))
    await db.commit()
    return user,conv

async def snapshot(db):
    return (await db.execute(select(MealItem.id,MealItem.meal_id,MealItem.amount_g,MealItem.calories,MealItem.protein_g,MealItem.carbs_g,MealItem.fat_g).order_by(MealItem.id))).all()

@pytest.mark.asyncio
@pytest.mark.parametrize('variant',['original-r1','original-r2','missing','zero','repair'])
async def test_original_task_end_to_end_no_hidden_write(db_session,session_factory,monkeypatch,variant):
    user,conv=await seed(db_session,variant);uid=user.id
    before=await snapshot(db_session);install(monkeypatch,session_factory)
    archived=DATA['rows'][1 if variant=='original-r2' else 0]
    route=next(c['payload'] for c in archived['calls'] if c['name']=='submit_semantic_route')
    sequence=[function_message('submit_semantic_route',route,'route')]
    if variant=='repair':sequence.append(function_message('submit_nutrition_history_query',plan(date_comparisons=[]),'plan-bad'))
    sequence.append(function_message('submit_nutrition_history_query',plan(),'plan-good'))
    requests=scripted_http(monkeypatch,sequence)
    evidence={}
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(uid)}) as client:
            body=await run(client,session_factory,conv,MESSAGE)
            evidence['body']=body;evidence['requests']=requests
            assert body['status']=='completed' and body['error_code'] is None,body
            assert len(requests)==len(sequence)
            trace=body['execution_trace'];assert trace['evidence_contract']['status']=='complete'
            assert trace['evidence_contract']['required_tools']==['nutrition.list_history']
            reply=body['reply'];result=next(c['data'] for c in body['cards'] if c['type']=='nutrition.list_history')
            assert '其他用户食物' not in json.dumps(result,ensure_ascii=False)
            assert result['scope']['as_of']=='2026-10-09' and result['scope']['logged_dates']==['2026-09-27','2026-10-02']
            if variant=='missing':assert '无法计算两日差值' in reply and ' = ' not in reply
            elif variant=='zero':assert '0 − 0 = 0 g' in reply and '两日已记录量相同' in reply
            else:
                assert '2026-10-02已记录更多，差 12.2 g' in reply,reply
                for actual,gold in zip(result['days'],DATA['case']['query_gold'][0]['rows']):
                    assert actual['date']==gold['date']
                    for metric in ('calories','protein_g','carbs_g','fat_g'):assert actual['total_'+metric]==gold[metric]
            assert '不代表实际全天摄入' in reply
            second=(await client.get('/api/v1/agent/runs/'+body['id'])).json()
            assert second==body and len(requests)==len(sequence),'GET replay must not regenerate'
        async with session_factory() as db:
            calls=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==body['id']))).all()
            assert len(calls)==1 and calls[0].status=='completed' and calls[0].tool_name=='nutrition.list_history'
            assert calls[0].arguments_data['logged_dates']==['2026-09-27','2026-10-02']
            evidence['tool_audits']=[dict(tool=c.tool_name,status=c.status,arguments=c.arguments_data,result=c.result_data) for c in calls]
            stored=await db.get(AgentRun,body['id'])
            assert stored.execution_trace==trace and stored.model_usage==body['model_usage']
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==uid))==0
            evidence['unchanged_meals']=True;evidence['proposals']=0
    finally:
        app.dependency_overrides.clear()
        destination=os.environ.get('R79_EVIDENCE_DIR')
        if destination:
            (Path(destination)/('sql-task-'+variant+'-'+uuid4().hex+'.json')).write_text(json.dumps({'source':'synthetic fixture, scripted model, actual API/worker/SQL','variant':variant,**evidence},ensure_ascii=False,indent=2),encoding='utf8')

@pytest.mark.asyncio
@pytest.mark.parametrize('variant',['meal','future','calendar'])
async def test_actual_tool_selection_meals_future_and_calendar(db_session,variant):
    user,_=await seed(db_session)
    tool=build_read_tools(db_session,user_id=user.id,allowlist=['nutrition.list_history'])[0]
    dates=['2026-09-27','2026-10-02']
    args={'logged_dates':dates,'meal_types':['午餐'] if variant=='meal' else []}
    if variant=='calendar':
        dates=['2026-10-08','2026-10-09'];args={'start_date':dates[0],'end_date':dates[1]}
    with request_day(datetime(2026,10,1 if variant=='future' else 9,4,tzinfo=timezone.utc)):
        data=await tool.ainvoke(args)
    comp=DateComparison.model_validate(comparison(left_date=dates[0],right_date=dates[1]))
    reply=render_history(data,date_comparisons=[comp])
    if variant=='meal':assert '34.6' in reply and '17.4' in reply and '差 17.2 g' in reply and '餐次筛选：午餐' in reply
    elif variant=='future':assert '晚于查询截止日期' in reply and '无法计算两日差值' in reply and ' = ' not in reply
    else:assert '2026-10-09已记录更多，差 6.7 g' in reply
    assert '其他用户食物' not in json.dumps(data,ensure_ascii=False)
