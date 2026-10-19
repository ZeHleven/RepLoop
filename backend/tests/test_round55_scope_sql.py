"""Real worker/SQL/read + real HTTP serialization with an offline provider."""
import asyncio
import importlib
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func, text
from evals.postgres_test_fixtures import engine, session_factory, db_session
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.agent import AgentRun, AgentToolCall, AgentProposal
from app.models.meal import MealLog, MealItem
from app.services import agent_runtime as runtime, nutrition_history_reporting as reporting
from app.services.agent_intent import IntentResolverOutcome
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.auth import create_access_token
from test_agent_domain_proposals import _context
from test_round49_required_evidence import resolution
from test_round54_task_evidence import query_plan

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    yield

ANSWER='2026-10-08午餐记录：米饭蛋白质2.4克、鸡胸肉37.2克，合计39.6克。这只是该餐记录，全天完整性未知。一般搭配建议：可增加一种蔬菜。'

def provider(monkeypatch, failure):
    requests=[]
    for module in ('httpx','httpx2'):
        mod=importlib.import_module(module)
        original=mod.AsyncClient.send
        async def send(client, request, _mod=mod, _original=original, **kwargs):
            if request.url.host=='test':
                return await _original(client,request,**kwargs)
            assert request.url.host=='api.deepseek.com'
            body=json.loads(request.content);requests.append(body)
            assert body['thinking']=={'type':'disabled'}
            assert not body.get('tools'), 'Evidence handoff must not allow duplicate queries'
            if failure:
                return _mod.Response(400,json={'error':{'type':'invalid_request_error','message':'offline failure'}},request=request)
            return _mod.Response(200,json={'id':'offline-r54','object':'chat.completion','model':'deepseek-flash',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':ANSWER}}],
                'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30}},request=request)
        monkeypatch.setattr(mod.AsyncClient,'send',send)
    return requests

@pytest.mark.asyncio
@pytest.mark.parametrize('encoding',['range','discrete'])
@pytest.mark.parametrize('repair',[False,True])
@pytest.mark.parametrize('mode',['narrative','structured'])
async def test_equivalent_plans_execute_exact_scoped_sql_once(db_session,session_factory,monkeypatch,encoding,repair,mode):
    scenario,count='normal',1
    user,_,conv,_=await _context(db_session,'r55-'+uuid4().hex)
    foreign,_,_,_=await _context(db_session,'r54-foreign-'+uuid4().hex)
    for owner,day,meal_type,foods in [
        (user.id,'2026-10-08','午餐',[('米饭',100,116,2.4,25.9,.3),('鸡胸肉',120,186,37.2,0,2.4)]),
        (user.id,'2026-10-08','晚餐',[('隔离晚餐',100,900,90,0,0)]),
        (user.id,'2026-10-07','午餐',[('隔离日期',100,800,80,0,0)]),
        (foreign.id,'2026-10-08','午餐',[('隔离用户',100,700,70,0,0)]),
    ]:
        meal=MealLog(user_id=owner,logged_at=date.fromisoformat(day),meal_type=meal_type)
        db_session.add(meal);await db_session.flush()
        for name,amount,calories,protein,carbs,fat in foods:
            db_session.add(MealItem(meal_id=meal.id,food_name=name,amount_g=amount,calories=calories,protein_g=protein,carbs_g=carbs,fat_g=fat))
    await db_session.commit()
    async def snapshot(db):
        return (await db.execute(select(MealItem.food_name,MealItem.amount_g,MealItem.calories,MealItem.protein_g,MealItem.carbs_g,MealItem.fat_g).order_by(MealItem.id))).all()
    before=await snapshot(db_session)
    async def override():
        async with session_factory() as db:yield db
    app.dependency_overrides[get_db]=override
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    monkeypatch.setattr(settings,'AGENT_PLANNED_EXECUTION_ENABLED',False)
    message='核对2026-10-08午餐食品的蛋白质，给一条建议。' if mode=='narrative' else '查询2026-10-08午餐蛋白质。'
    r=resolution(request_kind='query',evidence_requirements=['nutrition_history'],resolved_query=message)
    monkeypatch.setattr(runtime,'resolve_intent_with_fallback',AsyncMock(return_value=IntentResolverOutcome(resolution=r,source='model',attempt_count=1)))
    payload=query_plan(response_mode=mode)
    if encoding=='discrete':payload.update(start_date='',end_date='',logged_dates=['2026-10-08'])
    previous={**payload,'response_mode':'invalid-mode'}
    if encoding=='range':previous.update(start_date='',end_date='',logged_dates=['2026-10-08'])
    else:previous.update(start_date='2026-10-08',end_date='2026-10-08',logged_dates=[])
    planner=AsyncMock(side_effect=[SimpleNamespace(payload=x,parse_error=None) for x in ([previous,payload] if repair else [payload])])
    monkeypatch.setattr(reporting,'structured_chat_completion',planner)
    requests=provider(monkeypatch,scenario=='provider_failure')
    build=runtime.build_read_tools;calls=[];raw_results=[]
    def builder(db,**kwargs):
        tools=build(db,**kwargs)
        assert [t.name for t in tools]==['nutrition_list_history']
        coroutine=tools[0].coroutine
        async def invoke(**args):
            calls.append(args)
            if scenario=='sql_error':await db.execute(text('SELECT 1/0'))
            if scenario=='sql_timeout_once' and len(calls)==1:
                try:await db.execute(text('SELECT 1/0'))
                except Exception as exc:raise TimeoutError() from exc
            result=await coroutine(**args)
            raw_results.append(result)
            return result
        tools[0].coroutine=invoke
        return tools
    monkeypatch.setattr(runtime,'build_read_tools',builder)
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'Authorization':'Bearer '+create_access_token(user.id)}) as client:
            response=await client.post('/api/v1/agent/runs',json={'conversation_id':conv.id,'message':message,'client_request_id':uuid4().hex})
            assert response.status_code==202,response.text
            rid=response.json()['run_id']
            async with session_factory() as db:
                run=await db.get(AgentRun,rid);run.queued_at=datetime(2026,10,9,4,tzinfo=timezone.utc);await db.commit()
                assert await claim_next_agent_run(db)==rid
            await asyncio.wait_for(process_agent_run(session_factory,rid),15)
            body=(await client.get('/api/v1/agent/runs/'+rid)).json()
            assert body['status']==('failed' if scenario=='provider_failure' else 'completed'),body
            trace=body['execution_trace']
            assert trace['budget_usage']['tool_calls']==count
            assert len(trace['observations'])==count
            assert trace['terminal_action']==('failed' if scenario=='provider_failure' else 'safe_stop' if scenario=='sql_error' else 'answer')
            assert (await client.get('/api/v1/agent/runs/'+rid)).json()==body
            if scenario not in ('provider_failure','sql_error'):
                assert '39.6' in body['reply']
                if mode=='narrative':
                    receipt=trace['nutrition_delivery']
                    assert receipt['requested_dates']==['2026-10-08'] and receipt['meal_type']=='午餐'
                    assert receipt['max_actions']==1 and len(receipt['actions'])==1
                    assert {(f['food_name'],f['amount_g'],f['protein_g']) for f in receipt['item_facts']}=={('米饭',100,2.4),('鸡胸肉',120,37.2)}
                    assert receipt['source_call_ids']==[trace['observations'][0]['call_id']]
                    assert receipt['source_fingerprints']==[trace['observations'][0]['result_fingerprint']]
                    assert '2.4g' in body['reply'] and '37.2g' in body['reply'] and '隔离' not in body['reply']
                    assert '没有以这些记录判断实际全天或个人蛋白质是否达标' in body['reply']
                else:assert trace.get('nutrition_delivery') is None
                saved=(await client.get('/api/v1/agent/conversations/'+conv.id+'/messages')).json()
                assert next(m['content'] for m in saved if m.get('run_id')==rid and m['role']=='assistant')==body['reply']
        async with session_factory() as db:
            audits=(await db.scalars(select(AgentToolCall).where(AgentToolCall.run_id==rid).order_by(AgentToolCall.created_at))).all()
            assert len(audits)==count
            assert sum(a.status=='failed' for a in audits)==int(scenario in ('sql_timeout_once','sql_error'))
            for audit in audits:
                assert audit.arguments_data=={k:payload[k] for k in ('days','start_date','end_date','logged_dates','meal_types')}
            successful=[a for a in audits if a.status=='completed']
            if successful:
                raw=raw_results[0]
                assert successful[0].result_data==runtime._audit_result_summary('nutrition.list_history',json.dumps(raw,ensure_ascii=False))
                assert raw['scope']['as_of']=='2026-10-09'
                assert raw['scope']['meal_types']==['午餐']
                assert len(raw['days'])==1 and raw['days'][0]['total_protein_g']==39.6
                assert '隔离' not in json.dumps(raw,ensure_ascii=False)
                assert 'assessment_evidence_version' not in raw
            assert await snapshot(db)==before
            assert await db.scalar(select(func.count()).select_from(AgentProposal).where(AgentProposal.user_id==user.id))==0
        assert len(calls)==count and planner.await_count==(2 if repair else 1)
        assert len(requests)==int(mode=='narrative' and scenario!='sql_error')
        if requests:
            tool_messages=[m for m in requests[0]['messages'] if m['role']=='tool']
            assert len(tool_messages)==count
            evidence=json.loads(tool_messages[-1]['content'])
            assert evidence['business_data']==raw
            foods=[f for f in evidence['facts'] if f['kind']=='food']
            assert {f['subject']:f['values']['protein_g'] for f in foods}=={
                '2026-10-08 午餐 米饭 100g':2.4,'2026-10-08 午餐 鸡胸肉 120g':37.2,
            }, foods
            assert evidence['boundaries']['whole_day_completeness']=='unknown'
    finally:
        app.dependency_overrides.clear()

def query_plan_arguments():
    return {k:query_plan()[k] for k in ('days','start_date','end_date','logged_dates','meal_types')}
