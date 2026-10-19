"""Scripted route/answer HTTP with original tasks and actual API, worker, SQL."""
import copy,json,os
from datetime import date,datetime,timezone
from pathlib import Path
from uuid import uuid4
import pytest,pytest_asyncio
from httpx import AsyncClient,ASGITransport
from sqlalchemy import select,func
from evals.postgres_test_fixtures import engine,session_factory,db_session
from app.main import app
from app.config import settings
from app.models.meal import MealLog,MealItem
from app.models.agent import AgentRun,AgentProposal,AgentArtifact,AgentToolCall
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context
from test_round76_source_scope_sql import install,run,function_message
from test_round57_read_delivery import scripted_http
from round81_support import DATA,ROWS,corrected

@pytest_asyncio.fixture(autouse=True)
async def setup_db():yield

async def seed(db,id):
    user,profile,conv,_=await _context(db,'r81-'+uuid4().hex)
    other,_,_,_=await _context(db,'r81-other-'+uuid4().hex)
    case=next(c for c in DATA['cases'] if c['id']==id[:3])
    for key,value in case['profile'].items():setattr(profile,key,value)
    foods={f['name']:f for f in case['foods']}
    for raw in case['meals']:
        meal=MealLog(user_id=user.id,logged_at=date.fromisoformat(raw['date']),meal_type=raw['meal_type']);db.add(meal);await db.flush()
        for name,grams in raw['items'].items():
            food=foods[name];db.add(MealItem(meal_id=meal.id,food_name=name,amount_g=grams,
                calories=round(food['calories']*grams/100,1),protein_g=round(food['protein_g']*grams/100,1),
                carbs_g=round(food['carbs_g']*grams/100,1),fat_g=round(food['fat_g']*grams/100,1)))
    foreign=MealLog(user_id=other.id,logged_at=date(2026,10,9),meal_type='晚餐');db.add(foreign);await db.flush()
    db.add(MealItem(meal_id=foreign.id,food_name='隔离用户食物',amount_g=500,calories=999,protein_g=888,carbs_g=777,fat_g=66))
    await db.commit();return user,conv,other

async def snapshot(db):
    return (await db.execute(select(MealLog.id,MealLog.user_id,MealLog.logged_at,MealLog.meal_type,MealItem.id,MealItem.food_name,MealItem.amount_g,MealItem.calories,MealItem.protein_g,MealItem.carbs_g,MealItem.fat_g).join(MealItem).order_by(MealLog.id,MealItem.id))).all()

def evidence(name,value):
    if dest:=os.getenv('R81_EVIDENCE_DIR'):
        (Path(dest)/('api-'+name+'-'+uuid4().hex+'.json')).write_text(json.dumps({'model':'scripted HTTP; not real model quality',**value},ensure_ascii=False,indent=2),encoding='utf8')

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['U01-r1','U01-r2','U03-r1','U03-r2'])
async def test_original_failure_repaired_with_actual_data_and_durable_receipt(db_session,session_factory,monkeypatch,id):
    user,conv,other=await seed(db_session,id);before=await snapshot(db_session);row=ROWS[id]
    install(monkeypatch,session_factory)
    draft=row['model_answers'][-1] if id=='U01-r2' else row['body']['reply']
    sequence=[function_message('submit_semantic_route',copy.deepcopy(row['route']),'route'),{'role':'assistant','content':draft},{'role':'assistant','content':corrected(id)}]
    requests=scripted_http(monkeypatch,sequence);saved={'id':id,'source_reply':draft}
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            body=await run(client,session_factory,conv,row['message']);saved['body']=body
            assert body['error_code'] is None and body['status']=='completed',body
            assert body['reply']==corrected(id),body
            trace=body['execution_trace'];receipt=trace['nutrition_goal_delivery']
            assert receipt['status']=='corrected' and receipt['repair_attempts']==1 and receipt['draft']==draft
            assert trace['budget_usage']['model_calls']==2 and body['model_usage']['http_requests']==3
            assert len(requests)==3
            assert 'nutrition_response_goals' in json.dumps(requests[1],ensure_ascii=False)
            assert 'nutrition_revision' in json.dumps(requests[2],ensure_ascii=False) and not requests[2].get('tools')
            if id.startswith('U01'):
                contract=trace['evidence_contract'];assert contract['status']=='complete'
                assert 'nutrition.list_history' not in contract['required_tools'] and 'nutrition.list_history' in contract['optional_tools']
                assert {a['tool_id'] for a in trace['actions']}=={'nutrition.get_recent_context','profile.get_summary'}
                data=next(c['data'] for c in body['cards'] if c['type']=='nutrition.get_recent_context')
                assert len(data['recent_days'])==9 and sum(len(d['meals'])==1 for d in data['recent_days'])==6
                assert max(d['date'] for d in data['recent_days'])=='2026-10-09'
                assert '隔离用户食物' not in json.dumps(data,ensure_ascii=False)
            else:assert not trace['actions'] and not body['cards']
            replay=(await client.get('/api/v1/agent/runs/'+body['id'])).json();assert replay==body
            history=(await client.get('/api/v1/agent/conversations/'+conv.id+'/messages')).json()
            assert next(m['content'] for m in history if m.get('run_id')==body['id'] and m['role']=='assistant')==body['reply']
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(other.id)}) as stranger:
                assert (await stranger.get('/api/v1/agent/runs/'+body['id'])).status_code==404
        async with session_factory() as db:
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
            assert await db.scalar(select(func.count()).select_from(AgentArtifact).where(AgentArtifact.user_id==user.id))==0
            record=await db.get(AgentRun,body['id']);assert record.execution_trace==trace
            calls=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==body['id']))).all()
            assert len(calls)==(2 if id.startswith('U01') else 0) and all(c.status=='completed' for c in calls)
            saved['tool_audits']=[dict(tool=c.tool_name,status=c.status,arguments=c.arguments_data,result=c.result_data) for c in calls]
        saved.update(passed=True,business_unchanged=True,no_proposal=True,other_user_denied=True)
    finally:
        app.dependency_overrides.clear();saved['requests']=requests;evidence(id,saved)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['already_complete','still_missing','budget','protocol','new_adequacy','assessment_lost_goal','provider_missing_repair'])
async def test_actual_no_tool_delivery_boundaries(db_session,session_factory,monkeypatch,mode):
    id='U03-r1';row=ROWS[id];user,conv,_=await seed(db_session,id);before=await snapshot(db_session)
    install(monkeypatch,session_factory)
    if mode=='budget':monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',1)
    if mode=='new_adequacy':monkeypatch.setattr(settings,'AGENT_MAX_MODEL_CALLS',2)
    first=corrected(id) if mode=='already_complete' else corrected(id)+'你的蛋白质已经足够。' if mode=='assessment_lost_goal' else row['body']['reply']
    last=(row['body']['reply'] if mode=='still_missing' else '<tool_call>fake</tool_call>' if mode=='protocol'
          else corrected(id)+'你的蛋白质已经足够，不用再加。' if mode in {'new_adequacy','provider_missing_repair'}
          else '目前无法判断蛋白质是否达标。请提供个人目标。' if mode=='assessment_lost_goal' else corrected(id))
    sequence=[function_message('submit_semantic_route',copy.deepcopy(row['route']),'route'),{'role':'assistant','content':first}]
    if mode not in {'budget','already_complete'}:sequence.append({'role':'assistant','content':last})
    requests=scripted_http(monkeypatch,sequence)
    saved={'mode':mode,'response_sequence':sequence}
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            body=await run(client,session_factory,conv,row['message']);trace=body['execution_trace']
            saved['body']=body
            if mode=='already_complete':assert body['error_code'] is None and body['reply']==first and 'nutrition_goal_delivery' not in trace
            else:
                assert body['error_code'] and body['reply']!=last,body
                if mode=='new_adequacy':assert trace['assessment_delivery']['status']=='blocked'
                elif mode=='provider_missing_repair':assert trace['assessment_delivery']['status']=='interrupted'
                else:assert trace['nutrition_goal_delivery']['status']=='blocked'
                if mode=='assessment_lost_goal':
                    assert trace['assessment_delivery']['status']=='corrected'
                    assert trace['nutrition_goal_delivery']['final_findings'] and trace['nutrition_goal_delivery']['repair_attempts']==0
            # The configured SDK retries a failed client attempt once. These
            # two local fixture refusals are not provider HTTP responses.
            assert len(requests)==len(sequence)+(2 if mode=='provider_missing_repair' else 0) and not trace['actions']
            if mode=='provider_missing_repair':
                assert body['model_usage']['client_attempts']==5 and body['model_usage']['http_responses']==3
                assert body['model_usage']['local_errors']==2 and body['model_usage']['transport_attempts']==0
        async with session_factory() as db:
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
        saved.update(passed=True,business_unchanged=True)
    finally:
        app.dependency_overrides.clear();saved['requests']=requests;evidence(mode,saved)
