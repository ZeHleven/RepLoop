"""Archived live failures plus independent read/write and repair boundaries."""
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from app.config import settings
from app.services.agent_intent import resolve_intent, IntentResolution
from app.services.agent_intent_model import IntentRouteDecision, _route_to_resolution, resolve_intent_with_fallback, _intent_user_content
from app.schemas.agent_task import TaskSnapshot, ConversationTask, TaskRequirement, TaskUpdate
from app.services.agent_task_state import advance_task_state, is_calendar_query_revision
from app.services.daily_meal_optimizer import _occurrences, DailyMealOptimizationError

DATA=json.loads((Path('/workspace/backend/tests/fixtures/round34/archived-failures.json') if Path('/workspace').exists() else Path(__file__).parent/'fixtures/round34/archived-failures.json').read_text(encoding='utf-8'))
READS=[r for r in DATA['diagnostics']['rule_conflicts'] if r['instance'].startswith('DQ')]

@pytest.mark.parametrize('row',READS,ids=lambda r:r['instance'])
@pytest.mark.asyncio
async def test_archived_correct_read_routes_are_accepted(row):
    message=row['text']
    assert resolve_intent(message).request_kind=='query'
    route=IntentRouteDecision.model_validate(row['provider_routes'][0])
    with patch.object(settings,'DEEPSEEK_API_KEY','offline'), patch('app.services.agent_intent_model._invoke_model_route',new=AsyncMock(return_value=route)):
        outcome=await resolve_intent_with_fallback(message,use_model=True)
    assert outcome.source=='model' and not outcome.understanding_failed
    assert outcome.attempt_count==1 and outcome.resolution.requested_effect=='read'
    assert not outcome.resolution.change_requests

@pytest.mark.parametrize('message',[
    '记录今天午餐米饭200克。','查饮食记录，然后记录今天午餐米饭200克。',
    '查记录日平均热量，并保存当前方案。','查询饮食记录并删除今天的午餐。',
    '把训练记录下来。','查看过去训练记录，开始今天的训练。',
    '记录里程5公里。','记录中午米饭200克。','记录日常午餐米饭200克。','记录日期为2026年10月9日的午餐米饭200克。',
])
@pytest.mark.asyncio
async def test_positive_writes_cannot_borrow_read_route(message):
    assert resolve_intent(message).request_kind=='mutation'
    read=IntentResolution(primary_intent='nutrition_history_query',intent_domain='nutrition',request_kind='query',requested_effect='read',confidence=.95,resolved_query=message)
    with patch.object(settings,'DEEPSEEK_API_KEY','offline'), patch('app.services.agent_intent_model._invoke_model_intent',new=AsyncMock(return_value=read)):
        outcome=await resolve_intent_with_fallback(message,use_model=True)
    assert outcome.understanding_failed and outcome.error_category in {'semantic_mutation_structure_missing','semantic_artifact_save_requires_mutation'}

def query_context():
    state=TaskSnapshot(active_task_id='q',tasks=[ConversationTask(id='q',last_run_id='run1',request='查询2026年10月5日至7日已完成的训练',phase='responded')])
    return [{'role':'task_state','content':state.model_dump_json()},{'role':'assistant','content':'没有记录','query_context':{'successful_query':True,'source_run_id':'run1','primary_intent':'workout_history_query','request_kind':'query','requested_effect':'read','risk_level':'low'}}]

@pytest.mark.parametrize('suffix,ok',[
    ('',True),('，并删除这条训练记录',False),('，保存修改',False),('，把训练改成已完成',False),
])
def test_date_status_revision_whole_message(suffix,ok):
    text='日期改成2026年10月3日，只查进行中的训练，给日期状态'+suffix
    r=IntentResolution(primary_intent='workout_history_query',intent_domain='workout_history',request_kind='query',requested_effect='read',confidence=.95,task_update=TaskUpdate(action='continue',task_id='q',trigger=text))
    assert is_calendar_query_revision(query_context(),r,text) is ok
    r.task_update.task_id='other'
    assert not is_calendar_query_revision(query_context(),r,text)

@pytest.mark.parametrize('row',DATA['drafts'],ids=lambda r:r['instance']+'-'+str(r['index']))
def test_archived_infeasible_portions_identify_item_and_bounds(row):
    with pytest.raises(DailyMealOptimizationError) as caught:
        _occurrences(row['draft']['meals'],row['request']['food_candidates'])
    error=caught.value
    assert error.violated_metrics==('portion_bounds',)
    details=getattr(error,'constraint_violations',())
    assert details
    peanut=next(x for x in details if x['food_id']=='r31-peanut')
    assert peanut['feasible_min_g']>peanut['feasible_max_g']
    assert peanut['minimum_g']==25 and peanut['relative_max']==1.5
    item=row['draft']['meals'][peanut['meal_index']]['items'][peanut['item_index']]
    assert item['amount_g']==peanut['suggested_g']
    fixed=copy.deepcopy(row['draft']['meals'])
    for problem in details: fixed[problem['meal_index']]['items'][problem['item_index']]['amount_g']=problem['minimum_g']
    assert _occurrences(fixed,row['request']['food_candidates'])

@pytest.mark.parametrize('bad',DATA['diagnostics']['requirement_failure'])
def test_preserve_removal_is_still_rejected_and_retry_explains_repair(bad):
    update=TaskUpdate.model_validate(bad['route']['task_update'])
    previous=DATA['prior_route']
    state=TaskSnapshot(active_task_id=update.task_id,tasks=[ConversationTask(id=update.task_id,request=previous['normalized_request'],last_run_id=update.task_id,requirements=[TaskRequirement(key=r['key'],quote=r['quote'],source_run_id=update.task_id) for r in previous['task_update']['requirements']])])
    message='这份待确认提案只把卧推建议重量改为22.5公斤，组数和深蹲休息都保留。'
    with pytest.raises(ValueError,match='task_preserved_requirement_cannot_remove'):
        advance_task_state(state,update,message=message,run_id='new',normalized_request=bad['route']['normalized_request'])
    content=_intent_user_content(message,context_messages=[{'role':'task_state','content':state.model_dump_json()}],pending_clarification=None,repair_error='task_preserved_requirement_cannot_remove')
    assert '不要把保留解释为恢复数据库原计划' in content
    corrected=update.model_copy(update={'requirements':[r for r in update.requirements if r.key=='bench_weight']})
    final=advance_task_state(state,corrected,message=message,run_id='new',normalized_request=previous['normalized_request'])
    old={r.key:r.quote for r in state.tasks[0].requirements};new={r.key:r.quote for r in final.tasks[0].requirements}
    assert new['bench_sets']==old['bench_sets'] and new['squat_rest']==old['squat_rest']
    assert '22.5' in new['bench_weight']


@pytest.mark.asyncio
@pytest.mark.parametrize('recovers',[True,False])
async def test_generation_receives_item_constraints_and_keeps_two_attempt_budget(db_session,recovers):
    from test_agent_daily_meal_plans import _daily_context, _target_compliant_draft, _completion
    from app.services.agent_daily_meal_plans import generate_daily_meal_artifact, DailyMealPlanError
    user,conversation,run,foods=await _daily_context(db_session,'r34-repair-'+str(recovers).lower())
    # The shared suite retains earlier catalogue rows; keep these fixtures in the top 80.
    for food in foods:
        food.name_zh='000-r34-'+food.name_zh
        food.is_common_in_china=True
    await db_session.commit()
    valid=_target_compliant_draft(foods).model_dump(mode='json')
    invalid=copy.deepcopy(valid)
    invalid['meals'][0]['items'][0]['amount_g']=10
    calls=AsyncMock(side_effect=[_completion(invalid),_completion(valid if recovers else invalid)])
    with patch('app.services.agent_daily_meal_plans.structured_chat_completion',new=calls):
        if recovers:
            result=await generate_daily_meal_artifact(db_session,user_id=user.id,conversation_id=conversation.id,run_id=run.id,user_message='安排今天三餐，仅生成方案')
            assert result.artifact.payload_data['portion_policy']=='meal-usability-v1.1'
            audits=result.optimization_attempts
        else:
            with pytest.raises(DailyMealPlanError) as caught:
                await generate_daily_meal_artifact(db_session,user_id=user.id,conversation_id=conversation.id,run_id=run.id,user_message='安排今天三餐，仅生成方案')
            audits=caught.value.optimization_attempts
    assert calls.await_count==2
    repair=json.loads(calls.await_args_list[1].args[0][1]['content'])['repair_context']
    assert repair['infeasible_metrics']==['portion_bounds']
    problem=repair['optimization_failures'][0]['constraint_violations'][0]
    assert problem['food_id']==invalid['meals'][0]['items'][0]['food_id']
    assert problem['suggested_g']==10 and problem['feasible_min_g']==25 and problem['feasible_max_g']==15
    assert repair['invalid_draft']['meals'][0]['items'][0]['amount_g']==10
    assert audits[0].result_data()['constraint_violations'][0]==problem

@pytest.mark.parametrize('message',[
    '查记录日平均热量，然后记录今天晚餐豆腐200克。',
    '没有记录的话，记录今天早餐燕麦50克。',
])
def test_noun_exclusion_never_erases_independent_record_command(message):
    assert resolve_intent(message).request_kind=='mutation'

def test_multiple_food_conflicts_reported_together():
    row=DATA['drafts'][0];meals=copy.deepcopy(row['draft']['meals'])
    meals[0]['items'][0]['amount_g']=5
    with pytest.raises(DailyMealOptimizationError) as caught:
        _occurrences(meals,row['request']['food_candidates'])
    details=caught.value.constraint_violations
    assert len(details)>=2 and len({(d['meal_index'],d['item_index']) for d in details})==len(details)
