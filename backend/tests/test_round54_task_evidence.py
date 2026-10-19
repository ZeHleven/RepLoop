"""R53 retained failures and scope/authority counterexamples, no model network."""
import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import settings
from app.services import agent_intent_model as model
from app.services import nutrition_history_reporting as reporting
from app.services.agent_intent import resolve_intent, route_tools, required_read_tools
from app.services.agent_intent_model import IntentRouteDecision, resolve_intent_with_fallback
from app.services.agent_evidence_contract import build_evidence_contract
from app.services.business_clock import BUSINESS_DATE
from app.services.history_status_scope import parse_history_status_scope

ROWS=json.loads((Path(__file__).parent/'fixtures/round54/archived-paths.json').read_text(encoding='utf8'))['rows']
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-r54')
    monkeypatch.setattr(model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No network')))
    monkeypatch.setattr(reporting,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No network')))
    yield
    BUSINESS_DATE.reset(token)

@pytest.mark.asyncio
@pytest.mark.parametrize('id,expected',[
    ('WX03-r2',{'plan.get_active','health.get_screening_summary'}),
    ('WX04-r2',{'plan.get_active','health.get_screening_summary'}),
    ('CW02-r2',{'plan.get_active','workout.list_history','workout.get_progress'}),
    ('CW03-r1',{'plan.get_active','workout.list_history','workout.get_progress'}),
    ('CW03-r2',{'plan.get_active','workout.list_history','workout.get_progress'}),
    ('CW04-r1',{'plan.get_active','workout.list_history','workout.get_progress'}),
    ('CW04-r2',{'plan.get_active','workout.list_history','workout.get_progress'}),
])
async def test_baseline_archived_task_has_exact_core_obligations(id,expected,monkeypatch):
    r=row(id)
    for raw in r['routes']:
        monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
        out=await resolve_intent_with_fallback(r['message'],use_model=True)
        assert not out.understanding_failed,out.error_category
        resolution=out.resolution
        assert set(required_read_tools(resolution))==expected
        contract=build_evidence_contract(resolution,route_tools(resolution),budget=4)
        assert contract.status=='pending' and set(contract.required_tools)==expected
        assert getattr(contract,'scope_quotes',[]), 'Retain original task evidence for required/optional choice'
        assert all(q in r['message'] for q in contract.scope_quotes)

@pytest.mark.parametrize('message',[
    '请分别核对已完成训练次数和计划完成训练日。',
    '请统计计划完成训练日，并列出实际训练记录。',
    '请核对计划已完成训练日数。',
])
def test_baseline_count_properties_are_read_objects(message):
    assert resolve_intent(message).requested_effect=='read'

@pytest.mark.parametrize('message',[
    '请分别核对本周截至今天已完成的训练次数和计划完成训练日；跳过、进行中都不要算作已完成，请解释两项差别。',
    '请分别统计已完成的训练，跳过与进行中都不计入已完成。',
])
def test_baseline_status_operands_do_not_confuse_comparison_word(message):
    scope=parse_history_status_scope(message)
    assert scope.complete and scope.statuses==('completed',)

def query_plan(**updates):
    return {'supported':True,'days':30,'start_date':'2026-10-08','end_date':'2026-10-08',
            'logged_dates':[],'meal_types':['午餐'],'metrics':['protein_g'],
            'operations':['list'],'comparison_targets':[],'comparison_scope':'meal',
            'comparison_scope_quote':'',**updates}

@pytest.mark.asyncio
@pytest.mark.parametrize('id,meals,metrics',[
    ('NX05-r1',[],['protein_g','carbs_g','fat_g']),
    ('NX07-r1',['午餐'],['protein_g']),
    ('NX07-r2',['午餐'],['protein_g']),
])
async def test_baseline_scoped_narrative_read_plan_retains_full_query(id,meals,metrics,monkeypatch):
    r=row(id);raw=query_plan(meal_types=meals,metrics=metrics,response_mode='narrative')
    call=AsyncMock(return_value=SimpleNamespace(payload=raw,parse_error=None))
    monkeypatch.setattr(reporting,'structured_chat_completion',call)
    plan=await reporting.plan_nutrition_history(message=r['message'],resolved_query=r['routes'][0]['normalized_request'],today=date(2026,10,9))
    assert getattr(plan,'response_mode',None)=='narrative'
    assert reporting.history_arguments(plan)['meal_types']==meals
    assert reporting.history_arguments(plan)['start_date']=='2026-10-08'
    assert call.await_count==1

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['query','assessment'])
@pytest.mark.parametrize('risk',['low','medium'])
async def test_temporary_task_core_is_invariant_to_model_label(monkeypatch,kind,risk):
    r=row('WX04-r2');raw=r['routes'][0]
    raw.update(request_kind=kind,risk_level=risk,read_targets=['workout_daily_context','workout_progress','profile_summary','health_screening','active_plan'])
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
    out=await resolve_intent_with_fallback(r['message'],use_model=True)
    assert not out.understanding_failed
    assert set(required_read_tools(out.resolution))=={'plan.get_active','health.get_screening_summary'}
    assert len(route_tools(out.resolution))==4
    assert build_evidence_contract(out.resolution,route_tools(out.resolution),budget=2).status=='pending'
    assert build_evidence_contract(out.resolution,['health.get_screening_summary'],budget=4).reason=='authority_missing'

@pytest.mark.asyncio
async def test_explicit_additional_goals_remain_required_and_budgeted(monkeypatch):
    r=row('WX04-r2');raw=r['routes'][0]
    message=r['message']+'还要核对我的体重资料，分析训练容量趋势，并说明今天该练什么。'
    raw.update(normalized_request=message,read_targets=['active_plan','health_screening'])
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert not out.understanding_failed
    assert set(required_read_tools(out.resolution))=={'plan.get_active','health.get_screening_summary','profile.get_summary','workout.get_progress','workout.get_daily_context'}
    assert route_tools(out.resolution)==[]
    assert build_evidence_contract(out.resolution,[],budget=4).reason=='budget_exceeded'

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
    '核对已完成训练次数后，请完成当前训练。',
    '把当前计划完成训练日设为2。',
    '请分别标记为已完成并保存训练。',
    '给我临时训练建议，然后修改卧推重量为20公斤。',
    '不要只看计划训练日，请完成这次训练。',
])
async def test_scope_policy_cannot_launder_actual_writes(monkeypatch,message):
    raw=copy.deepcopy(row('CW04-r1')['routes'][0]);raw.update(normalized_request=message)
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert out.understanding_failed and route_tools(out.resolution)==[]

@pytest.mark.parametrize('message',[
    '不要排除跳过，查询已完成。',
    '不是不看进行中，查看已完成。',
    '已完成也不要算作已完成。',
    '查询未完成的训练。',
])
def test_uncertain_or_contradictory_status_not_guessed(message):
    assert not parse_history_status_scope(message).complete

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['structured','narrative'])
async def test_narrative_does_not_turn_unsupported_query_into_success(monkeypatch,mode):
    raw=query_plan(supported=False,response_mode=mode)
    monkeypatch.setattr(reporting,'structured_chat_completion',AsyncMock(return_value=SimpleNamespace(payload=raw,parse_error=None)))
    with pytest.raises(reporting.NutritionHistoryPlanError,match='不支持'):
        await reporting.plan_nutrition_history(message='只查昨天午餐中蛋白质超过50克的食物，再给建议。',resolved_query='同原请求',today=date(2026,10,9))

@pytest.mark.asyncio
async def test_narrative_cannot_erase_explicit_calendar_selection(monkeypatch):
    raw=query_plan(response_mode='narrative',start_date='',end_date='',meal_types=['午餐'])
    monkeypatch.setattr(reporting,'structured_chat_completion',AsyncMock(return_value=SimpleNamespace(payload=raw,parse_error=None)))
    with pytest.raises(reporting.NutritionHistoryPlanError):
        await reporting.plan_nutrition_history(message='核对2026-10-08午餐食品的蛋白质，给一条建议。',resolved_query='同原请求',today=date(2026,10,9))

@pytest.mark.asyncio
@pytest.mark.parametrize('risk',['low','medium'])
async def test_query_optional_batch_cannot_exceed_call_budget(monkeypatch,risk):
    from langchain_core.messages import AIMessage
    from test_round49_required_evidence import install,resolution,invoke
    monkeypatch.setattr(settings,'AGENT_MAX_TOOL_CALLS',1)
    response=AIMessage(content='',tool_calls=[{'name':'nutrition_list_history','id':'h'+str(i),'args':{}} for i in range(2)])
    calls=install(monkeypatch,responses=[response])
    result=await invoke(resolution(request_kind='query',risk_level=risk))
    assert calls==[] and result.get('read_error_code')=='read_tool_budget_exhausted'

@pytest.mark.parametrize('suffix',[
    '的前一天', '之后', '之前', '起',
])
def test_date_fragment_with_modifier_is_not_a_proven_narrative_selection(suffix):
    from app.services.nutrition_query_scope import explicit_requested_selection
    assert explicit_requested_selection(f'核对2026-10-08{suffix}午餐食品的蛋白质，给一条建议。',date(2026,10,9)) is None

@pytest.mark.parametrize('message',[
    '假设核对2026-10-08午餐食品的蛋白质，给一条建议。',
    '核对2026-10-08午餐食品的蛋白质，改查2026-10-07。',
    '核对2026-10-08午餐食品的蛋白质，再核对2026-10-07午餐食品的蛋白质。',
    '不要核对2026-10-08午餐食品的蛋白质，给一条建议。',
])
def test_narrative_selection_requires_the_whole_original_request(message):
    from app.services.nutrition_query_scope import explicit_requested_selection
    assert explicit_requested_selection(message,date(2026,10,9)) is None

@pytest.mark.asyncio
async def test_narrative_cannot_erase_explicit_meal_selection(monkeypatch):
    raw=query_plan(response_mode='narrative',meal_types=[])
    monkeypatch.setattr(reporting,'structured_chat_completion',AsyncMock(return_value=SimpleNamespace(payload=raw,parse_error=None)))
    with pytest.raises(reporting.NutritionHistoryPlanError):
        await reporting.plan_nutrition_history(message='核对2026-10-08午餐食品的蛋白质，给一条建议。',resolved_query='同原请求',today=date(2026,10,9))
