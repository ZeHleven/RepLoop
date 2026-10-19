"""R51 failures, semantic boundary pairs and source-bound single-day comparisons."""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import settings
from app.services import agent_intent_model, nutrition_history_reporting as nutrition
from app.services.agent_intent import resolve_intent, route_tools
from app.services.agent_intent_model import IntentRouteDecision, resolve_intent_with_fallback
from app.schemas.nutrition_history import NutritionHistoryPlan
from app.services.business_clock import BUSINESS_DATE

ARCHIVE=json.loads((Path(__file__).parent/'fixtures/round52/archived-routes.json').read_text(encoding='utf8'))['rows']
BASE=dict(supported=True,days=30,start_date='2026-10-08',end_date='2026-10-08',logged_dates=[],meal_types=[],metrics=['protein_g','carbs_g','fat_g'],operations=['list'])
TARGETS=[{'metric':'protein_g','value':39.6,'source_quote':'蛋白质39.6克'},{'metric':'carbs_g','value':22.4,'source_quote':'碳水22.4克'},{'metric':'fat_g','value':5.1,'source_quote':'脂肪5.1克'}]
MESSAGE='昨天只吃了这一餐，所有食物饮料都记全了。我的自定每日目标是蛋白质39.6克、碳水22.4克、脂肪5.1克，只核对三项差额，不要食物建议。'
DAY=dict(BASE,comparison_targets=TARGETS,comparison_scope='day',comparison_scope_quote='每日目标')

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    token=BUSINESS_DATE.set(date(2026,10,9))
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','offline-r52')
    monkeypatch.setattr(agent_intent_model,'structured_chat_completion',AsyncMock(side_effect=AssertionError('External model forbidden')))
    yield
    BUSINESS_DATE.reset(token)

@pytest.mark.parametrize('row',ARCHIVE,ids=lambda r:r['id'])
@pytest.mark.asyncio
async def test_baseline_archived_advice_routes(row,monkeypatch):
    for raw in row['routes']:
        monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
        out=await resolve_intent_with_fallback(row['message'],use_model=True)
        assert not out.understanding_failed, out.error_category
        assert out.attempt_count==1 and out.resolution.requested_effect=='read'
        assert not out.resolution.change_requests
        assert 'health.get_screening_summary' in route_tools(out.resolution)

@pytest.mark.parametrize('noun',['不适','疼痛','伤病','过敏','病史'])
def test_baseline_health_record_noun_is_not_write(noun):
    assert resolve_intent(f'已有{noun}记录，请结合计划给我一周安排，只要建议。').requested_effect=='read'

@pytest.mark.parametrize('message',[
    '已有不适记录，请把训练频率改为每周2天。',
    '已有疼痛记录，先看一下，然后删除该记录。',
    '请把不适记录到健康档案。',
    '请将过敏记录下来。',
    '请把伤病记录为膝盖疼痛。',
    '请记录今天训练后的不适。',
    '已有病史记录，但不得不修改当前计划。',
    '先给一周建议，再把训练天数改成2天。',
])
@pytest.mark.asyncio
async def test_health_noun_does_not_mask_independent_writes(message,monkeypatch):
    route=IntentRouteDecision(intent_domain='workout_plan',request_kind='query',requested_effect='read',requested_output='answer',read_targets=['active_plan'],decision_action=None,artifact_action=None,normalized_request=message,risk_level='low',confidence=.99)
    monkeypatch.setattr(agent_intent_model,'_invoke_model_route',AsyncMock(return_value=route))
    out=await resolve_intent_with_fallback(message,use_model=True)
    assert out.understanding_failed and not route_tools(out.resolution)

def mock_plan(monkeypatch,payload):
    call=AsyncMock(return_value=SimpleNamespace(payload=payload,parse_error=None))
    monkeypatch.setattr(nutrition,'structured_chat_completion',call)
    return call

def history(protein=39.6):
    return {'scope':{'selection':'calendar_range','start_date':'2026-10-08','end_date':'2026-10-08','as_of':'2026-10-09','meal_types':[],'returned_dates':1,'first_logged_date':'2026-10-08','last_logged_date':'2026-10-08'},'days':[{'date':'2026-10-08','total_calories':302,'total_protein_g':protein,'total_carbs_g':22.4,'total_fat_g':5.1}]}

@pytest.mark.asyncio
async def test_baseline_daily_comparison_uses_complete_date_scope(monkeypatch):
    call=mock_plan(monkeypatch,DAY)
    plan=await nutrition.plan_nutrition_history(message=MESSAGE,resolved_query=MESSAGE,today=date(2026,10,9))
    assert call.await_count==1 and not nutrition.history_arguments(plan)['meal_types']
    answer=nutrition.render_history(history(),metrics=plan.metrics,operations=plan.operations,comparison_targets=plan.comparison_targets)
    assert answer.count('差额 0 g')==3
    assert '单日全部餐次' in answer and '已记录' in answer and '个人' in answer
    assert '仅比较这一餐' not in answer and '已保存' not in answer

@pytest.mark.asyncio
async def test_unknown_day_completeness_can_compare_recorded_amount_only(monkeypatch):
    message='昨天的饮食没有记齐，我的每日目标是蛋白质39.6克、碳水22.4克、脂肪5.1克，只核对已记录数量与目标的差额。'
    mock_plan(monkeypatch,DAY)
    plan=await nutrition.plan_nutrition_history(message=message,resolved_query=message,today=date(2026,10,9))
    answer=nutrition.render_history(history(38.25),comparison_targets=plan.comparison_targets)
    assert '低于目标 1.35 g' in answer
    assert '不证明全天记录完整' in answer and '实际摄入' in answer
    assert '未达标' not in answer and '摄入不足' not in answer

@pytest.mark.parametrize('change',[
    {'meal_types':['午餐']}, {'meal_types':['早餐','午餐','晚餐']},
    {'end_date':'2026-10-09'}, {'start_date':'','end_date':'','logged_dates':['2026-10-08','2026-10-09']},
    {'comparison_scope_quote':'每天目标'}, {'comparison_scope_quote':''},
    {'comparison_targets':TARGETS[:2]}, {'comparison_targets':[dict(TARGETS[0],value=40),*TARGETS[1:]]},
    {'comparison_scope':'meal'}, {'operations':['mean']},
])
@pytest.mark.asyncio
async def test_daily_comparison_cannot_shrink_expand_or_invent_source(monkeypatch,change):
    mock_plan(monkeypatch,{**DAY,**change})
    with pytest.raises(nutrition.NutritionHistoryPlanError):
        await nutrition.plan_nutrition_history(message=MESSAGE,resolved_query=MESSAGE,today=date(2026,10,9))

@pytest.mark.parametrize('message',[
    '昨天的记录只列出。假设每日目标是蛋白质39.6克、碳水22.4克、脂肪5.1克，也不要核对差额。',
    '朋友的每日目标是蛋白质39.6克、碳水22.4克、脂肪5.1克。请核对我昨天的记录，但不要用朋友的目标。',
    '我的每日目标不是蛋白质39.6克、碳水22.4克、脂肪5.1克。请核对昨天记录。',
    '如果我的每日目标是蛋白质39.6克、碳水22.4克、脂肪5.1克，请只核对昨天记录。',
    '昨天记录蛋白质39.6克、碳水22.4克、脂肪5.1克。核对差额，目标暂时没定。',
])
@pytest.mark.asyncio
async def test_nonpersonal_or_hypothetical_targets_not_promoted_to_asserted_goals(monkeypatch,message):
    mock_plan(monkeypatch,DAY)
    with pytest.raises(nutrition.NutritionHistoryPlanError):
        await nutrition.plan_nutrition_history(message=message,resolved_query=MESSAGE,today=date(2026,10,9))

@pytest.mark.asyncio
async def test_day_scope_must_not_replace_explicit_meal_scope(monkeypatch):
    message='我的每日目标是蛋白质39.6克、碳水22.4克、脂肪5.1克，只比较昨天午餐的记录，不要查询其他餐。'
    mock_plan(monkeypatch,DAY)
    with pytest.raises(nutrition.NutritionHistoryPlanError):
        await nutrition.plan_nutrition_history(message=message,resolved_query=message,today=date(2026,10,9))

def test_empty_day_not_compared_as_zero():
    data=history();data['days']=[];data['scope']['returned_dates']=0
    plan=NutritionHistoryPlan.model_validate(DAY)
    answer=nutrition.render_history(data,comparison_targets=plan.comparison_targets)
    assert '没有已记录' in answer and '差额' not in answer

def test_existing_meal_comparison_contract_stays_compatible():
    plan=NutritionHistoryPlan.model_validate({**BASE,'meal_types':['午餐'],'comparison_targets':TARGETS})
    data=history();data['scope']['meal_types']=['午餐']
    answer=nutrition.render_history(data,comparison_targets=plan.comparison_targets)
    assert '这一餐' in answer and answer.count('差额 0 g')==3

