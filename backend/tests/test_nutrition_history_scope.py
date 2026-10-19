import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models.user import User
from app.models.food import Food
from app.models.meal import MealLog, MealItem
from app.schemas.nutrition_history import NutritionHistoryArguments, NutritionHistoryPlan
from app.services.agent_intent import IntentResolution
from app.services.agent_tools import build_read_tools
from app.services.business_clock import request_day
from app.services.nutrition_history_reporting import render_history, plan_nutrition_history, supports_typed_nutrition_history
from app.services.ai_client import AIServiceError


def data_fixture():
    return json.loads((Path(__file__).parent/'fixtures/round33/nutrition-scope.json').read_text(encoding='utf-8'))


def resolution(**changes):
    return IntentResolution(primary_intent='nutrition_history_query', intent_domain='nutrition',
                            evidence_requirements=['nutrition_history'], resolved_query='查有记录日期并比较最高最低差',
                            confidence=1, **changes)


def test_archived_error_replaced_with_actual_scope_and_exact_comparison():
    fixture=data_fixture()
    assert '最近 30 天内' in fixture['reply']
    reply=render_history(fixture['data'], metrics=['calories'], operations=['list','range'])
    assert '最近30天' not in reply and '最近 30 天内' not in reply
    assert '2026-08-31' in reply and '2026-10-08' in reply
    assert '最高：2026-10-03，370 千卡' in reply
    assert '最低：2026-08-31，130 千卡' in reply
    assert '相差 240 千卡' in reply


def test_ties_zero_gap_and_recorded_date_mean():
    data=data_fixture()['data']
    for row in data['days']: row['total_calories']=100
    reply=render_history(data,metrics=['calories'],operations=['range','mean','sum'])
    assert '最高：2026-10-08、2026-10-03、2026-08-31，100' in reply
    assert '相差 0 千卡' in reply and '均值 100 千卡（除以3' in reply
    assert '合计 300 千卡' in reply


def test_calendar_and_empty_results_do_not_claim_latest_records():
    data=data_fixture()['data']
    data['scope'].update(selection='calendar_range',start_date='2026-09-01',end_date='2026-09-30')
    data['days']=[]
    reply=render_history(data,operations=['mean','range'])
    assert '请求日历范围：2026-09-01 至 2026-09-30' in reply
    assert '没有已记录' in reply and '均值 0' not in reply and '最近最多' not in reply


def test_metric_selection_keeps_comparison_units_and_no_extra_macros():
    data=data_fixture()['data']
    reply=render_history(data,metrics=['protein_g'],operations=['list','sum'])
    assert '蛋白质' in reply and '千卡' not in reply and '碳水' not in reply


@pytest.mark.parametrize('arguments',[
    {'days':0},{'days':31},{'start_date':'2026-10-01'},
    {'start_date':'2026-10-02','end_date':'2026-10-01'},
    {'start_date':'2025-01-01','end_date':'2026-10-01'},
    {'logged_dates':['2026-02-30']},{'logged_dates':['2026-10-01','2026-10-01']},
    {'start_date':'2026-10-01','end_date':'2026-10-02','logged_dates':['2026-10-01']},
    {'meal_types':['夜宵']},{'meal_types':['午餐','午餐']},{'user_id':'other'},
])
def test_invalid_scopes_rejected(arguments):
    with pytest.raises(ValidationError): NutritionHistoryArguments.model_validate(arguments)


@pytest.mark.parametrize('changes,allowlist',[
    ({'request_kind':'assessment'},['nutrition.list_history']),
    ({'requested_effect':'update'},['nutrition.list_history']),
    ({'risk_level':'high'},['nutrition.list_history']),
    ({},['nutrition.list_history','profile.get_summary']),
])
def test_typed_path_never_expands_permission_or_swallows_mixed_goals(changes,allowlist):
    assert not supports_typed_nutrition_history(resolution(**changes),allowlist)


@pytest.mark.asyncio
@pytest.mark.parametrize('payload',[None, {'supported':True},
    {'supported':False,'metrics':['calories'],'operations':['list']}])
async def test_invalid_or_unsupported_plan_fails_before_tool_query(monkeypatch,payload):
    from app.services import nutrition_history_reporting as reporting
    monkeypatch.setattr(reporting,'structured_chat_completion',AsyncMock(return_value=SimpleNamespace(payload=payload)))
    with pytest.raises(AIServiceError):
        await plan_nutrition_history(message='任意条件',resolved_query='任意条件',today=date(2026,10,9))


@pytest.mark.asyncio
async def test_runtime_uses_parsed_conditions_and_never_model_rewrites_facts(monkeypatch):
    from app.services import agent_runtime, nutrition_history_reporting as reporting
    payload={'supported':True,'days':30,'metrics':['calories'],'operations':['list','range']}
    planner=AsyncMock(return_value=SimpleNamespace(payload=payload))
    monkeypatch.setattr(reporting,'structured_chat_completion',planner)
    invoke=AsyncMock(return_value=data_fixture()['data'])
    monkeypatch.setattr(agent_runtime,'build_read_tools',lambda *a,**k:[SimpleNamespace(name='nutrition_list_history',ainvoke=invoke)])
    monkeypatch.setattr(agent_runtime,'_build_model',Mock(side_effect=AssertionError('No free prose generator')))
    result=await agent_runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message='查日期并比较最高最低差',tool_allowlist=['nutrition.list_history'],resolution=resolution())
    assert result['response_mode']=='verified_nutrition_history_report'
    assert '相差 240 千卡' in result['messages'][-1].content
    assert result['report_cards'][0]['data']==data_fixture()['data']
    assert invoke.call_args.args[0]=={'days':30,'start_date':'','end_date':'','logged_dates':[],'meal_types':[]}


async def seed(db):
    uid='r33-'+uuid4().hex; other=uid+'-other'; fid=uid+'-food'
    db.add_all([User(id=u,email=u+'@example.invalid',password_hash='synthetic') for u in (uid,other)])
    db.add(Food(id=fid,name_zh='合成食品',category='grain',calories_per_100g=100,protein_g=10,carbs_g=10,fat_g=2,is_active=True))
    await db.flush()
    for owner,day,meal,grams in [(uid,'2026-08-31','早餐',100),(uid,'2026-10-03','午餐',200),(uid,'2026-10-03','晚餐',100),(uid,'2026-10-08','午餐',150),(uid,'2026-10-11','午餐',900),(other,'2026-10-03','午餐',999)]:
        row=MealLog(user_id=owner,logged_at=date.fromisoformat(day),meal_type=meal);db.add(row);await db.flush()
        db.add(MealItem(meal_id=row.id,food_id=fid,food_name='合成食品',amount_g=grams,calories=grams,protein_g=grams*.1,carbs_g=grams*.1,fat_g=grams*.02))
    await db.commit()
    return uid,fid


@pytest.mark.asyncio
@pytest.mark.parametrize('arguments,expected,selection',[
    ({'days':2},[('2026-10-08',150),('2026-10-03',300)],'latest_logged_dates'),
    ({'days':1,'start_date':'2026-08-01','end_date':'2026-10-11'},[('2026-10-08',150),('2026-10-03',300),('2026-08-31',100)],'calendar_range'),
    ({'logged_dates':['2026-08-31','2026-10-08']},[('2026-10-08',150),('2026-08-31',100)],'specific_dates'),
    ({'meal_types':['晚餐']},[('2026-10-03',100)],'latest_logged_dates'),
    ({'start_date':'2026-09-01','end_date':'2026-09-30'},[],'calendar_range'),
])
async def test_tool_scopes_sql_aggregation_future_and_owner_isolation(db_session,arguments,expected,selection):
    uid,_=await seed(db_session)
    tool=build_read_tools(db_session,user_id=uid,allowlist=['nutrition.list_history'])[0]
    with request_day(datetime(2026,10,9,tzinfo=timezone.utc)):
        result=await tool.ainvoke(arguments)
    assert [(row['date'],row['total_calories']) for row in result['days']]==expected
    assert result['scope']['selection']==selection
    assert result['scope']['meal_types']==arguments.get('meal_types',[])


@pytest.mark.asyncio
async def test_calendar_range_not_truncated_to_default_thirty_recorded_days(db_session):
    uid,fid=await seed(db_session)
    for i in range(35):
        meal=MealLog(user_id=uid,logged_at=date(2026,7,1)+timedelta(days=i),meal_type='午餐');db_session.add(meal);await db_session.flush()
        db_session.add(MealItem(meal_id=meal.id,food_id=fid,food_name='合成食品',amount_g=100,calories=100,protein_g=10,carbs_g=10,fat_g=2))
    await db_session.commit()
    tool=build_read_tools(db_session,user_id=uid,allowlist=['nutrition.list_history'])[0]
    with request_day(datetime(2026,10,9,tzinfo=timezone.utc)):
        result=await tool.ainvoke({'start_date':'2026-07-01','end_date':'2026-08-04'})
    assert len(result['days'])==35 and result['scope']['requested_limit'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('query,arguments', [
    ('查询2026-09-01至2026-09-05的饮食记录', {'start_date':'2026-09-01','end_date':'2026-09-05'}),
    ('查看昨晚的饮食记录', {'start_date':'2026-10-08','end_date':'2026-10-08','meal_types':['晚餐']}),
])
async def test_newly_supported_date_scope_preserved_in_runtime(monkeypatch,query,arguments):
    from app.services import agent_runtime, nutrition_history_reporting as reporting
    planner=AsyncMock(return_value=SimpleNamespace(payload={'supported':True,'metrics':['calories'],'operations':['list'],**arguments}))
    monkeypatch.setattr(reporting,'structured_chat_completion',planner)
    invoke=AsyncMock(return_value={'scope':{'selection':'calendar_range',**arguments,'as_of':'2026-10-09'},'days':[]})
    monkeypatch.setattr(agent_runtime,'build_read_tools',lambda *a,**k:[SimpleNamespace(name='nutrition_list_history',ainvoke=invoke)])
    monkeypatch.setattr(agent_runtime,'_build_model',Mock(side_effect=AssertionError('No free prose generator')))
    result=await agent_runtime.invoke_langchain_agent(None,user_id='synthetic',history=[],user_message=query,tool_allowlist=['nutrition.list_history'],resolution=resolution())
    assert result['response_mode']=='verified_nutrition_history_report'
    assert invoke.call_args.args[0]==NutritionHistoryArguments(**arguments).model_dump()
    assert arguments['start_date'] in result['messages'][-1].content
    assert arguments['end_date'] in result['messages'][-1].content
    if arguments.get('meal_types'): assert '晚餐' in result['messages'][-1].content


@pytest.mark.asyncio
async def test_real_request_uses_provider_strict_schema_not_local_optional_defaults(monkeypatch):
    import json
    import httpx
    from app.config import settings
    from app.services.ai_client import structured_chat_completion
    from app.schemas.nutrition_history import NutritionHistoryPlan
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-test-key')
    monkeypatch.setattr(settings,'DEEPSEEK_BASE_URL','https://api.deepseek.com')
    requests=[]
    payload={'supported':True,'days':30,'start_date':'','end_date':'','logged_dates':[],'meal_types':[],'metrics':['calories'],'operations':['list']}
    async def post(client,url,**kwargs):
        data=kwargs['json'];schema=data['tools'][0]['function']['parameters'];requests.append(schema)
        request=httpx.Request('POST',url)
        if set(schema['required'])!=set(schema['properties']):
            return httpx.Response(400,json={'error':{'message':'Required properties must match all properties in the object','type':'invalid_request_error'}},request=request)
        assert schema['additionalProperties'] is False
        assert not any('default' in field for field in schema['properties'].values())
        return httpx.Response(200,json={'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'type':'function','function':{'name':'submit_nutrition_history_query','arguments':json.dumps(payload)}}]}}]},request=request)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    # Same raw schema as the rejected live request must reproduce the 400.
    with pytest.raises(AIServiceError):
        await structured_chat_completion([],model=settings.AGENT_MODEL,max_tokens=1200,temperature=0,function_name='submit_nutrition_history_query',function_description='synthetic',json_schema=NutritionHistoryPlan.model_json_schema(),json_example=payload)
    plan=await plan_nutrition_history(message='最近记录热量',resolved_query='最近记录热量',today=date(2026,10,9))
    assert plan.model_dump()==dict(payload,comparison_targets=[],comparison_scope='meal',comparison_scope_quote='',response_mode='structured',date_comparisons=[])
    assert len(requests)==2
    assert set(requests[0]['required'])!=set(requests[0]['properties'])
    assert set(requests[1]['required'])==set(requests[1]['properties'])
