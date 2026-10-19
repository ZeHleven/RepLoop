"""Source, scope and arithmetic tests; no live model quality claim."""
import copy,json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from app.schemas.nutrition_history import NutritionHistoryPlan,DateComparison
from app.services import nutrition_history_reporting as reporting
from app.services.nutrition_date_comparison import grounded_date_comparisons
from app.services.structured_schema import strict_tool_schema
from round79_support import DATA,MESSAGE,comparison,plan,observed
TODAY=date(2026,10,9)

def grounded(message=MESSAGE,**changes):
    return grounded_date_comparisons(message,NutritionHistoryPlan.model_validate(plan(**changes)),TODAY)

def test_original_four_metrics_and_exact_decimal_difference():
    p=NutritionHistoryPlan.model_validate(plan());assert grounded()
    answer=reporting.render_history(observed(),metrics=p.metrics,operations=p.operations,date_comparisons=p.date_comparisons)
    assert '43.4 − 55.6 = -12.2 g' in answer and '2026-10-02已记录更多，差 12.2 g' in answer
    for value in ['618','43.4','87.4','11','616','55.6','66.1','14.8']:assert value in answer
    assert '不代表实际全天摄入' in answer and '未进食' in answer
    assert set(reporting.history_arguments(p))=={'days','start_date','end_date','logged_dates','meal_types'}

@pytest.mark.parametrize('changes',[
    {'logged_dates':['2026-09-27']},{'logged_dates':['2026-09-27','2026-10-02','2026-10-03']},
    {'logged_dates':[]},{'metrics':['calories']},{'operations':['sum']},{'response_mode':'narrative'},
    {'date_comparisons':[comparison(),comparison()]},
    {'date_comparisons':[comparison(right_date='2026-09-27')]},
    {'date_comparisons':[comparison(right_date='2026-02-30')]},
    {'date_comparisons':[comparison(right_date='2026-10-03')]},
    {'comparison_targets':[dict(metric='protein_g',value=60,source_quote='蛋白质60g')]},
])
def test_schema_cannot_hide_missing_or_extra_selection(changes):
    with pytest.raises(ValidationError):NutritionHistoryPlan.model_validate(plan(**changes))

@pytest.mark.parametrize('changes',[
    {'date_comparisons':[]},{'metrics':['protein_g']},
    {'date_comparisons':[comparison(left_date_quote='9月27日')]},
    {'date_comparisons':[comparison(left_date_quote='10月2日')]},
    {'date_comparisons':[comparison(comparison_quote='比较两天蛋白质差值')]},
    {'date_comparisons':[comparison(metric='fat_g')]},{'meal_types':['午餐']},
])
def test_cannot_omit_or_fabricate_source_obligations(changes):assert not grounded(**changes)

@pytest.mark.parametrize('message',[
    '如果'+MESSAGE,'假设'+MESSAGE,'朋友问：“'+MESSAGE+'”',
    '列2026年9月27日和10月2日的蛋白质，不要再比较蛋白质差多少。',
])
def test_noncurrent_or_negated_anchor_rejected(message):assert not grounded(message)

@pytest.mark.parametrize('left,right,lq,rq',[
    ('2026-10-08','2026-10-07','昨天','前天'),('2026-10-09','2026-10-10','今天','明天'),
    ('2026-09-27','2026-09-29','2026年9月27日','29日'),
    ('2025-12-31','2026-01-01','2025年12月31日','2026年1月1日'),
])
def test_explicit_relative_and_month_elision(left,right,lq,rq):
    message=f'列{lq}和{rq}蛋白质，再比较蛋白质差多少。'
    p=plan(logged_dates=[left,right],metrics=['protein_g'],date_comparisons=[comparison(left_date=left,right_date=right,left_date_quote=lq,right_date_quote=rq)])
    assert grounded(message,**p)

@pytest.mark.parametrize('message',[
    '列2025年12月31日和1月1日的蛋白质，再比较蛋白质差多少。',
    '列27日和29日的蛋白质，再比较蛋白质差多少。',
])
def test_unanchored_or_ambiguous_year_never_guessed(message):assert not grounded(message)

@pytest.mark.parametrize('sentence',[
    '2026年9月27日的蛋白质减去10月2日的蛋白质',
    '2026年9月27日比10月2日的蛋白质多多少',
    '列2026年9月27日和10月2日蛋白质，计算前者减去后者的蛋白质差值',
])
def test_direction_cannot_be_swapped(sentence):
    quote=sentence.split('，')[-1]
    c=comparison(comparison_quote=quote)
    assert grounded(sentence,metrics=['protein_g'],date_comparisons=[c])
    c.update(left_date='2026-10-02',right_date='2026-09-27',left_date_quote='10月2日',right_date_quote='2026年9月27日')
    assert not grounded(sentence,metrics=['protein_g'],date_comparisons=[c])

@pytest.mark.parametrize('metric,expected',[('calories','2 千卡'),('protein_g','-12.2 g'),('carbs_g','21.3 g'),('fat_g','-3.8 g')])
def test_all_metric_units_and_decimal_arithmetic(metric,expected):
    reply=reporting.render_history(observed(),date_comparisons=[DateComparison.model_validate(comparison(metric=metric))])
    assert '= '+expected in reply

@pytest.mark.parametrize('left,right,expected',[(0,0,'两日已记录量相同'),(43.4,43.4,'两日已记录量相同'),(0,12.2,'-12.2 g'),(55.6,43.4,'12.2 g')])
def test_recorded_zero_equal_and_reverse_values(left,right,expected):
    d=observed();d['days'][1]['total_protein_g']=left;d['days'][0]['total_protein_g']=right
    assert expected in reporting.render_history(d,date_comparisons=[DateComparison.model_validate(comparison())])

@pytest.mark.parametrize('keep',[0,1])
def test_missing_never_imputed_zero(keep):
    d=observed();d['days']=d['days'][:keep];d['scope']['returned_dates']=keep
    reply=reporting.render_history(d,date_comparisons=[DateComparison.model_validate(comparison())])
    assert '无法计算两日差值' in reply and '不能将缺记录当作0' in reply and ' = ' not in reply

@pytest.mark.parametrize('damage',['duplicate','outside','future','count','no_scope','nan','infinity','bool','negative','string','missing_value'])
def test_invalid_observation_never_becomes_arithmetic(damage):
    d=observed()
    if damage=='duplicate':d['days'].append(copy.deepcopy(d['days'][0]));d['scope']['returned_dates']=3
    elif damage=='outside':d['days'][0]['date']='2026-10-03'
    elif damage=='future':d['scope']['as_of']='2026-10-01'
    elif damage=='count':d['scope']['returned_dates']=1
    elif damage=='no_scope':d['scope']['selection']='latest_logged_dates'
    elif damage=='missing_value':del d['days'][0]['total_protein_g']
    else:d['days'][0]['total_protein_g']={'nan':float('nan'),'infinity':float('inf'),'bool':True,'negative':-1,'string':'55.6'}[damage]
    reply=reporting.render_history(d,date_comparisons=[DateComparison.model_validate(comparison())])
    assert '未通过校验' in reply and ' = ' not in reply and '已记录更多' not in reply

@pytest.mark.asyncio
@pytest.mark.parametrize('row',DATA['rows'],ids=lambda r:r['id'])
async def test_original_false_responses_are_not_rewritten_as_success(row,monkeypatch):
    mock=AsyncMock(side_effect=[SimpleNamespace(payload=c['payload']) for c in row['calls'] if c['name']=='submit_nutrition_history_query'])
    monkeypatch.setattr(reporting,'structured_chat_completion',mock)
    with pytest.raises(reporting.NutritionHistoryPlanError,match='暂不支持'):
        await reporting.plan_nutrition_history(message=MESSAGE,resolved_query=row['resolved_query'],today=TODAY)
    assert mock.await_count==2

@pytest.mark.asyncio
@pytest.mark.parametrize('first',['omitted','unsupported','wrong_date','wrong_direction'])
async def test_one_source_grounded_repair_then_complete_plan(first,monkeypatch):
    bad=plan()
    if first=='omitted':bad['date_comparisons']=[]
    elif first=='unsupported':bad['supported']=False;bad['date_comparisons']=[]
    elif first=='wrong_date':bad['date_comparisons'][0]['left_date_quote']='昨天'
    else:bad['date_comparisons'][0]['left_date_quote']='10月2日'
    mock=AsyncMock(side_effect=[SimpleNamespace(payload=p) for p in (bad,plan())]);monkeypatch.setattr(reporting,'structured_chat_completion',mock)
    parsed=await reporting.plan_nutrition_history(message=MESSAGE,resolved_query='不得依赖消解文本的正确性',today=TODAY)
    assert parsed.date_comparisons[0].metric=='protein_g' and mock.await_count==2
    assert 'repair_context' in json.loads(mock.call_args.args[0][1]['content'])

@pytest.mark.asyncio
async def test_omission_exhausts_bounded_repair(monkeypatch):
    mock=AsyncMock(return_value=SimpleNamespace(payload=plan(date_comparisons=[])));monkeypatch.setattr(reporting,'structured_chat_completion',mock)
    with pytest.raises(reporting.NutritionHistoryPlanError) as exc:
        await reporting.plan_nutrition_history(message=MESSAGE,resolved_query=MESSAGE,today=TODAY)
    assert exc.value.category=='nutrition_query_plan_invalid' and mock.await_count==2

def test_strict_schema_exposes_required_empty_array_and_closed_operand_shape():
    schema=strict_tool_schema(NutritionHistoryPlan)
    assert 'date_comparisons' in schema['required'] and schema['additionalProperties'] is False

def test_existing_extrema_does_not_require_directional_comparison():
    message='列2026年9月27日和10月2日的蛋白质，比较蛋白质最高和最低相差多少。'
    assert grounded(message,operations=['list','range'],date_comparisons=[])

def test_calendar_endpoints_cannot_drop_intermediate_dates():
    assert not grounded('列2026年9月27日到10月2日的蛋白质，再比较蛋白质差多少。')

@pytest.mark.parametrize('suffix',['请再给我饮食调整建议。','请推荐一下搭配。','这些记录是否达标？'])
def test_cannot_silently_drop_additional_delivery(suffix):assert not grounded(MESSAGE+suffix)

def test_explicitly_excluded_advice_is_not_a_missing_goal():assert grounded(MESSAGE+'不要给我建议。')

def test_multiple_compared_metrics_preserve_every_difference():
    message='列2026年9月27日和10月2日的热量、蛋白质、碳水和脂肪，再比较蛋白质和脂肪差多少。'
    comparisons=[comparison(metric=m,comparison_quote='再比较蛋白质和脂肪差多少') for m in ('protein_g','fat_g')]
    assert grounded(message,date_comparisons=comparisons)
    assert not grounded(message,date_comparisons=comparisons[:1])

def test_adjacent_calendar_range_and_meal_selection_grounded():
    message='列10月8日到10月9日的午餐蛋白质，再比较蛋白质差多少。'
    c=comparison(left_date='2026-10-08',right_date='2026-10-09',left_date_quote='10月8日',right_date_quote='10月9日')
    assert grounded(message,logged_dates=[],start_date='2026-10-08',end_date='2026-10-09',meal_types=['午餐'],date_comparisons=[c])
