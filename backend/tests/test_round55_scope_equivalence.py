"""Retained model decisions plus semantic scope and write boundary pairs."""
import copy
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import settings
from app.services import agent_intent_model as model, nutrition_history_reporting as reporting
from app.services.agent_intent import resolve_intent, route_tools, required_read_tools
from app.services.agent_intent_model import IntentRouteDecision, resolve_intent_with_fallback
from app.schemas.nutrition_history import NutritionHistoryPlan
from app.services.business_clock import BUSINESS_DATE
from test_round52_read_comparison import DAY, MESSAGE, history
from test_round54_task_evidence import query_plan

ROWS=json.loads((Path(__file__).parent/'fixtures/round55/archived-paths.json').read_text(encoding='utf8'))['rows']
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-r55')
    monkeypatch.setattr(model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No network')))
    monkeypatch.setattr(reporting,'structured_chat_completion',AsyncMock(side_effect=AssertionError('No network')))
    yield
    BUSINESS_DATE.reset(token)

def planner(monkeypatch,plans):
    call=AsyncMock(side_effect=[SimpleNamespace(payload=p,parse_error=None) for p in plans])
    monkeypatch.setattr(reporting,'structured_chat_completion',call)
    return call

async def run_plan(message):
    return await reporting.plan_nutrition_history(message=message,resolved_query=message,today=date(2026,10,9))

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['NX08-r1','NX08-r2','NX10-r1','NX10-r2','DX01-r2','DX02-r2'])
async def test_baseline_archived_valid_first_plan_is_not_rejected(id,monkeypatch):
    r=row(id);plans=[d['payload'] for d in r['decisions'] if d['name']=='submit_nutrition_history_query']
    call=planner(monkeypatch,plans)
    plan=await run_plan(r['message'])
    assert call.await_count==1
    assert plan.supported
    assert (plan.logged_dates==['2026-10-08']) or plan.start_date==plan.end_date=='2026-10-08'

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['CW05-r1','CW05-r2','WX09-r1','WX09-r2'])
async def test_baseline_archived_read_routes_and_core_evidence(id,monkeypatch):
    r=row(id)
    first=True
    for d in r['decisions']:
        if d['name']!='submit_semantic_route':continue
        monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(d['payload'])))
        out=await resolve_intent_with_fallback(r['message'],use_model=True)
        # This archived second attempt followed the old false block and
        # fabricated one verbatim quote. Preserve that real rejection; the
        # valid first attempt must now succeed without requesting a repair.
        if id=='CW05-r2' and not first:
            assert out.understanding_failed and out.error_category=='task_requirement_not_current_user_quote'
            assert not route_tools(out.resolution)
            continue
        first=False
        assert not out.understanding_failed,out.error_category
        assert out.attempt_count==1 and out.resolution.requested_effect=='read'
        required={'plan.get_active','health.get_screening_summary'} if id.startswith('WX') else {'plan.get_active','workout.list_history','workout.get_progress'}
        assert set(required_read_tools(out.resolution))==required
        assert required<=set(route_tools(out.resolution))

@pytest.mark.parametrize('message',[
    '请列出计划完成日及其含义。','请分别核对计划完成训练日和实际场次。',
    '根据已保存训练计划给我临时安排，不修改计划。','请看已保存计划，给一周建议。',
])
def test_baseline_count_and_saved_nouns_are_read_only(message):
    assert resolve_intent(message).requested_effect=='read'

@pytest.mark.asyncio
@pytest.mark.parametrize('message',[
    '请保存计划。','请把训练记录下来。','请完成当前训练。',
    '把计划完成日设为2天。','根据已保存训练计划，把卧推重量改为20公斤。',
    '请核对计划完成日，然后删除该计划。','先看已保存计划，再保存新的训练计划。',
    '不要只看计划完成日，请完成这次训练。','我不得不修改已保存计划。',
])
async def test_noun_shields_never_mask_independent_write(message,monkeypatch):
    raw=copy.deepcopy(row('WX09-r1')['decisions'][0]['payload'])
    raw.update(normalized_request=message,task_update=None)
    monkeypatch.setattr(model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert out.understanding_failed and not route_tools(out.resolution)

@pytest.mark.asyncio
@pytest.mark.parametrize('reverse',[False,True])
@pytest.mark.parametrize('days',[1,3,30])
@pytest.mark.parametrize('capability',[False,True])
async def test_equivalent_date_encodings_can_repair_without_changing_selection(monkeypatch,reverse,days,capability):
    from datetime import timedelta
    dates=[(date(2026,9,1)+timedelta(days=i)).isoformat() for i in range(days)]
    first=query_plan(start_date=dates[0],end_date=dates[-1])
    second=query_plan(start_date='',end_date='',logged_dates=list(reversed(dates)))
    if reverse:first,second=second,first
    if capability:first['supported']=False
    else:first['response_mode']='bad'
    call=planner(monkeypatch,[first,second])
    result=await run_plan('请核对原文所指日期的午餐蛋白质。')
    assert result.supported and call.await_count==2

@pytest.mark.asyncio
@pytest.mark.parametrize('change',[
    dict(start_date='',end_date='',logged_dates=[]),
    dict(start_date='2026-10-07'),dict(end_date='2026-10-09'),
    dict(start_date='',end_date='',logged_dates=['2026-10-07','2026-10-08']),
    dict(meal_types=[]),dict(meal_types=['晚餐']),dict(meal_types=['午餐','晚餐']),
])
async def test_valid_previous_scope_cannot_be_dropped_or_changed_without_source(monkeypatch,change):
    first=query_plan(response_mode='bad');second=query_plan(**change)
    planner(monkeypatch,[first,second])
    with pytest.raises(reporting.NutritionHistoryPlanError):await run_plan('请查询所选日期的午餐记录。')

@pytest.mark.asyncio
async def test_discrete_dates_cannot_be_replaced_by_spanning_range(monkeypatch):
    first=query_plan(start_date='',end_date='',logged_dates=['2026-10-06','2026-10-08'],response_mode='bad')
    planner(monkeypatch,[first,query_plan(start_date='2026-10-06',end_date='2026-10-08')])
    with pytest.raises(reporting.NutritionHistoryPlanError):await run_plan('请核对所选日期的午餐。')

@pytest.mark.asyncio
async def test_source_proven_selection_can_correct_a_previous_wrong_day(monkeypatch):
    call=planner(monkeypatch,[query_plan(start_date='2026-10-07',end_date='2026-10-07'),query_plan(start_date='',end_date='',logged_dates=['2026-10-08'])])
    result=await run_plan('核对2026-10-08午餐食品的蛋白质，给一条建议。')
    assert result.logged_dates==['2026-10-08'] and call.await_count==2

@pytest.mark.asyncio
async def test_source_explicit_meal_can_correct_a_previous_wrong_meal(monkeypatch):
    call=planner(monkeypatch,[query_plan(meal_types=['晚餐']),query_plan()])
    result=await run_plan('核对2026-10-08午餐食品的蛋白质，给一条建议。')
    assert result.meal_types==['午餐'] and call.await_count==2

@pytest.mark.asyncio
@pytest.mark.parametrize('operations',[['list'],['sum'],['sum','list']])
async def test_single_day_sum_comparison_delivers_grounded_numbers(monkeypatch,operations):
    call=planner(monkeypatch,[{**DAY,'operations':operations}])
    result=await run_plan(MESSAGE)
    answer=reporting.render_history(history(),metrics=result.metrics,operations=result.operations,comparison_targets=result.comparison_targets)
    assert call.await_count==1 and answer.count('差额 0 g')==3
    assert '已记录 39.6 g，给定目标 39.6 g' in answer
    assert '不证明全天记录完整' in answer

@pytest.mark.parametrize('change',[
    {'operations':['mean']},{'operations':['list','range']},
    {'operations':['sum'],'end_date':'2026-10-09'},
    {'operations':['sum'],'meal_types':['午餐']},
])
def test_comparison_sum_does_not_expand_to_multiple_days_or_wrong_scope(change):
    with pytest.raises(ValueError):NutritionHistoryPlan.model_validate({**DAY,**change})
