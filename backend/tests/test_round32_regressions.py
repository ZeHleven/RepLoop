import copy
import json
from pathlib import Path
import pytest
from app.services.daily_meal_optimizer import optimize_daily_meal_amounts, DailyMealOptimizationError
from app.services.agent_query_reports import _unbounded_nutrition_listing
from evals.meal_usability_scoring import quality_errors

DATA=json.loads((Path(__file__).parent / 'fixtures/round32/archived-counterexample.json').read_text(encoding='utf-8'))
FOODS={('r31-'+f['id']):f for f in DATA['foods']}
CANDIDATES=[{**f,'id':fid,'calories_per_100g':f['calories']} for fid,f in FOODS.items()]

def test_independent_policy_rejects_archived_false_positive():
    errors=quality_errors(DATA['final'],DATA['draft']['meals'],FOODS)
    assert set(errors)>={'portion_bounds','portion_drift','meal_energy_share'}
    assert not quality_errors(DATA['draft']['meals'],DATA['draft']['meals'],FOODS)

@pytest.mark.parametrize('mode',['ideal','acceptable'])
def test_a46_archived_draft_never_redistributed_into_extreme_meals(mode):
    out=optimize_daily_meal_amounts(meals=DATA['draft']['meals'],food_candidates=CANDIDATES,existing_totals={},targets=DATA['targets'],mode=mode,time_limit_seconds=3)
    assert not quality_errors(out.meals,DATA['draft']['meals'],FOODS)

@pytest.mark.parametrize('query',[
    '列出用户最近记录的饮食历史，按日期展示当天总热量；仅查询历史记录，不提供饮食建议。',
    '请列出最近有记录的饮食日期和每天总热量，不要给建议',
    '查询近期饮食记录，按日期汇总每天的总热量，只查询，不修改',
])
def test_a18_unbounded_listing_daily_calories(query):
    assert _unbounded_nutrition_listing(query)

@pytest.mark.parametrize('query',[
    '列出最近30天饮食记录','列出2026-09-09至2026-10-09饮食记录',
    '列出最近两次饮食记录','最近饮食午餐总热量','比较最近饮食日期热量',
    '最近饮食记录并给建议','修改最近饮食记录','最近30条饮食记录',
    '本周饮食总热量','列出饮食记录蛋白质最高的日期',
])
def test_a18_preserves_selectors_and_extra_goals(query):
    assert not _unbounded_nutrition_listing(query)

def test_oil_only_main_is_infeasible():
    with pytest.raises(DailyMealOptimizationError):
        optimize_daily_meal_amounts(meals=[{'meal_type':'晚餐','items':[{'food_id':'r31-oil','amount_g':20}]}],food_candidates=CANDIDATES,existing_totals={},targets=DATA['targets'],mode='acceptable')

def test_policy_catches_two_meal_and_snack_imbalance():
    meals=[{'meal_type':m,'items':[{'food_id':'r31-rice','amount_g':g}]} for m,g in [('午餐',400),('晚餐',25),('加餐',100)]]
    assert set(quality_errors(meals,copy.deepcopy(meals),FOODS))>={'meal_energy_share','snack_energy_share'}
