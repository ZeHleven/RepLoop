"""Source arithmetic and boundaries; scripted responses do not count as TSR."""
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from app.services.assessment_grounding import project_evidence
from app.services.agent_nutrition_contributions import day_contributions
from app.services.agent_training_constraints import training_schedule, adjustment_budget
from app.services.agent_assessment_delivery import recover_assessment_answer
from app.services.agent_read_delivery import ReadDeliveryBudget

DATA=json.loads((Path(__file__).parent/'fixtures/round71/archived.json').read_text(encoding='utf8'))


def observed(run):
    return {'messages':[ToolMessage(name=t['tool'].replace('.','_'),tool_call_id=t['attempt_id'],content=json.dumps(t['result'],ensure_ascii=False))
                        for t in run['tool_attempts'] if t['status']=='success']}


def nutrition():
    run=DATA['NA05-r2']
    tool=next(t for t in run['tool_attempts'] if t['tool'].startswith('nutrition.'))
    return tool['tool'],copy.deepcopy(tool['result'])


def ledger(tool,data):
    return project_evidence(tool,data)['boundaries']['food_contributions_by_date']


def resolve(data,path):
    for key in path.split('/')[1:]:data=data[int(key)] if isinstance(data,list) else data[key]
    return data


def test_archived_nutrition_attribution_independent_sums():
    tool,data=nutrition();before=copy.deepcopy(data);days=ledger(tool,data)
    entry=next(d for d in days if d['date']=='2026-10-08')
    foods={f['identity']['food_name']:f for f in entry['foods']}
    assert foods['鸡胸']['values']['protein_g']==93
    assert foods['米饭']['values']['protein_g']==9
    assert sum(f['values']['protein_g'] for f in foods.values())==102
    assert entry['reconciliation']['fat_g']['item_sum']==13.8
    for f in foods.values():
        for metric,paths in f['sources'].items():
            assert sum(resolve(data,path) for path in paths)==pytest.approx(f['values'][metric])
    meal_facts=[f for f in project_evidence(tool,data)['facts'] if f['kind']=='meal']
    assert all(f['values']['fat_g']==4.6 for f in meal_facts)
    assert data==before


def one_day():
    return {'date':'2026-10-08','total_protein_g':8,'meals':[{'id':'m1','meal_type':'午餐','items':[
        {'id':'a','food_id':'x','food_name':'食物甲','amount_g':100,'protein_g':3},
        {'id':'b','food_id':'y','food_name':'食物甲','amount_g':200,'protein_g':5}]}]}


@pytest.mark.parametrize('mutation',['different_id','different_version','missing','negative','nan','boolean','mismatch','empty','duplicate_item','duplicate_meal','malformed'])
def test_contribution_identity_unknown_and_inconsistent_data(mutation):
    day=one_day();items=day['meals'][0]['items']
    if mutation=='different_version':
        items[1]['food_id']='x';items[0]['custom_food_version']=1;items[1]['custom_food_version']=2
    elif mutation=='missing':items[1].pop('protein_g')
    elif mutation=='negative':items[1]['protein_g']=-1
    elif mutation=='nan':items[1]['protein_g']=float('nan')
    elif mutation=='boolean':items[1]['protein_g']=True
    elif mutation=='mismatch':day['total_protein_g']=99
    elif mutation=='empty':day['meals']=[]
    elif mutation=='duplicate_item':items[1]['id']='a'
    elif mutation=='duplicate_meal':day['meals'].append(copy.deepcopy(day['meals'][0]))
    elif mutation=='malformed':day['meals'][0]['meal_type']=[]
    result=day_contributions(day,'#',['午餐'])
    if mutation in {'duplicate_item','duplicate_meal','malformed'}:
        assert result is None;return
    assert result['basis']=='selected_meal_records' and not result['complete_intake_certified']
    assert not result['cross_observation_addition_allowed']
    if mutation in {'missing','negative','nan','boolean','empty'}:
        assert result['reconciliation']['protein_g']['item_sum'] is None
        assert result['reconciliation']['protein_g']['matches'] is None
    elif mutation=='mismatch':assert result['reconciliation']['protein_g']['matches'] is False
    else:assert len(result['foods'])==2 and result['reconciliation']['protein_g']['matches'] is True


def test_dates_and_queries_never_merge():
    a=one_day();b=copy.deepcopy(a);b['date']='2026-10-09'
    data={'days':[a,b],'scope':{'meal_types':['午餐']}}
    result=ledger('nutrition.list_history',data)
    assert len(result)==2 and [r['date'] for r in result]==['2026-10-08','2026-10-09']
    assert all(r['reconciliation']['protein_g']['item_sum']==8 for r in result)
    assert ledger('nutrition.list_history',data)==result  # separate observations are independent


def plan(days,sets,exercises=2):
    return {'found':True,'plan':{'is_active':True,'days_per_week':days,'exercises':[
        {'day_of_week':d,'sets':sets,'reps':'10','rest_seconds':90,'exercise_name':f'ex-{d}-{i}'}
        for d in range(1,days+1) for i in range(exercises)]}}


@pytest.mark.parametrize('days',[1,2,4,7])
@pytest.mark.parametrize('sets',[1,3,10])
@pytest.mark.parametrize('cap',[1,5,10,12,15,None])
def test_return_path_changes_only_one_set_and_respects_every_source_row(days,sets,cap):
    schedule=training_schedule(plan(days,sets));allocation=adjustment_budget(schedule,cap)
    bounds=allocation['return_progression'];row={r['source_index']:r for r in bounds['row_bounds']}
    state={i:r['temporary_sets'] for i,r in row.items()}
    for step in bounds['count_steps']:
        i=step['changed_source_index']
        assert step['from_sets']==state[i] and step['to_sets']==state[i]+1<=row[i]['ceiling_sets']
        state[i]+=1
        assert step['weekly_sets']==sum(state.values())<=schedule['weekly_sets']
        assert step['days_per_week']<=allocation['temporary_days']<=days
        assert all(v<=cap*60 for v in step['estimated_session_seconds'].values()) if cap is not None else True
        assert step['reps_and_weight_unchanged'] and step['conditions_required']
    assert bounds['remaining_source_indices']==[i for i,r in row.items() if state[i]<r['ceiling_sets']]
    assert not bounds['saved_weight_is_mandatory_target'] and not bounds['medical_clearance']
    assert bounds['different_temporary_selection_requires_recalculation']


def test_dropped_day_and_time_cap_cannot_be_silently_restored():
    bounds=adjustment_budget(training_schedule(plan(4,3)),10)['return_progression']
    assert bounds['remaining_requires_new_time_frequency_and_symptom_check']
    assert all(row['original_day'] in (1,2) for row in bounds['row_bounds'] if row['day_in_temporary_allocation'])
    assert all(step['days_per_week']<=2 for step in bounds['count_steps'])
    one=adjustment_budget(training_schedule(plan(1,1,1)),10)
    assert one['temporary_weekly_sets']==0 and one['return_progression']['count_steps']==[]
    assert one['return_progression']['remaining_source_indices']==[0]


def test_archived_return_plan_is_not_profile_frequency():
    r=DATA['WX32-r1'];data=next(t['result'] for t in r['tool_attempts'] if t['tool']=='plan.get_active')
    bounds=adjustment_budget(training_schedule(data),15)['return_progression']
    assert bounds['days_per_week_ceiling']==1 and bounds['weekly_set_ceiling']==6
    assert [(s['from_sets'],s['to_sets']) for s in bounds['count_steps']]==[(2,3)]
    assert bounds['remaining_source_indices']  # 6 work sets would exceed this time cap.


@pytest.mark.asyncio
async def test_assessment_repair_gets_contributions_with_original_source_values():
    run=DATA['NA05-r2'];result=observed(run)
    result['messages'].append(AIMessage(content='蛋白质已经够用了。'))
    model=AsyncMock();model.ainvoke.return_value=AIMessage(content='本回答仅验证离线来源传递，不代表完成业务任务。')
    await recover_assessment_answer(result,model=model,budget=ReadDeliveryBudget(2),query=run['turns'][0]['message'],history=[],system_prompt='offline')
    messages=model.ainvoke.call_args.args[0]
    wire=json.loads(messages[-1].content)
    n=next(o for o in wire['observations'] if o['tool'].startswith('nutrition.'))
    foods=n['data']['boundaries']['food_contributions_by_date'][0]['foods']
    assert {f['identity']['food_name']:f['values']['protein_g'] for f in foods}=={'鸡胸':93,'米饭':9}
    assert result['assessment_delivery']['draft']=='蛋白质已经够用了。'
