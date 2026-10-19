"""Evidence projection must preserve raw audit and actual HTTP tool protocol."""
import copy,json,math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage,ToolMessage,SystemMessage,HumanMessage
from test_round50_provider_evidence import install_http
from test_round49_required_evidence import resolution
from app.services import agent_runtime as runtime
from app.config import settings

ARCHIVE=json.loads((Path(__file__).parent/'fixtures/round51/archived-assessments.json').read_text(encoding='utf8'))['rows']

def module():
    from app.services import assessment_grounding
    return assessment_grounding

def source(row_id,tool):
    row=next(r for r in ARCHIVE if r['id']==row_id)
    return copy.deepcopy(next(t['result'] for t in row['tools'] if t['tool']==tool))

@pytest.mark.asyncio
@pytest.mark.parametrize('history',[[],[{'role':'user','content':'我的过敏约束还在。'},{'role':'assistant','content':'按已给约束继续。'}]])
async def test_http_grounding_preserves_raw_audit(monkeypatch,history):
    monkeypatch.setattr(settings,'DEEPSEEK_API_KEY','synthetic-offline')
    calls,requests=install_http(monkeypatch)
    result=await runtime.invoke_langchain_agent(None,user_id='synthetic-owner',history=history,user_message='评估昨天的记录',tool_allowlist=['profile.get_summary','nutrition.get_recent_context','nutrition.list_history'],resolution=resolution())
    assert len(requests)==2
    sent=[json.loads(m['content']) for m in requests[0]['messages'] if m['role']=='tool']
    assert all(x.get('assessment_evidence_version')==1 for x in sent),'Successful assessment evidence must reach the real HTTP model request'
    assert '数值营养目标的保存功能未提供' in requests[0]['messages'][0]['content']
    for m in result['messages']:
        if isinstance(m,ToolMessage):assert 'assessment_evidence_version' not in json.loads(m.content)
    assert len(runtime._tool_call_records(result,'run'))==len(calls)==3
    for old in history:assert any(m.get('content')==old['content'] for m in requests[0]['messages'])

def pointer(data,path):
    for key in path.removeprefix('#/').split('/'):
        data=data[int(key)] if isinstance(data,list) else data[key.replace('~1','/').replace('~0','~')]
    return data

@pytest.mark.parametrize('row',ARCHIVE,ids=lambda r:r['id'])
def test_archived_projection_is_source_bound_and_lossless(row):
    for tool in row['tools']:
        raw=copy.deepcopy(tool['result']);before=copy.deepcopy(raw)
        out=module().project_evidence(tool['tool'],raw)
        if out is None:continue
        assert out['business_data']==raw and raw==before
        for fact in out['facts']:
            for path in fact['subject_sources'].values():assert pointer(raw,path) is not None
            for key,value in fact['values'].items():
                operands=[pointer(raw,x) for x in fact['sources'][key]]
                if fact['operation']=='sum':assert math.isclose(value,sum(operands),abs_tol=1e-8)
                else:assert operands==[value]

def test_food_vs_meal_totals_and_counterfactuals():
    raw=source('NA11-r2','nutrition.list_history')
    out=module().project_evidence('nutrition.list_history',raw)
    chicken=next(f for f in out['facts'] if f['kind']=='food' and '鸡胸' in f['subject'])
    meal=next(f for f in out['facts'] if f['kind']=='meal')
    assert chicken['values']['protein_g']==31 and meal['values']['protein_g']==34
    assert pointer(raw,chicken['subject_sources']['food_name'])=='鸡胸'
    assert pointer(raw,chicken['subject_sources']['amount_g'])==100
    path=chicken['sources']['protein_g'][0]
    item=raw
    for key in path.removeprefix('#/').split('/')[:-1]:item=item[int(key)] if isinstance(item,list) else item[key]
    item['protein_g']=7.25
    changed=module().project_evidence('nutrition.list_history',raw)
    assert next(f for f in changed['facts'] if f['kind']=='meal')['values']['protein_g']==10.25

def test_future_is_excluded_not_absent():
    raw=source('NA12-r1','nutrition.list_history');out=module().project_evidence('nutrition.list_history',raw)
    assert out['boundaries']['future_record_existence']=='unknown'
    assert out['boundaries']['excluded_future']=={'start_date':'2026-10-10','end_date':'2026-10-10'}
    assert out['boundaries']['whole_day_completeness']=='unknown'
    assert out['business_data']['scope']['end_date']=='2026-10-10'

def test_missing_micro_fields_and_empty_not_zero():
    raw=source('NA06-r1','nutrition.list_history');out=module().project_evidence('nutrition.list_history',raw)
    assert out['facts']==[] and out['boundaries']['unlogged_intake_is_zero'] is False
    assert out['boundaries']['micronutrient_or_fiber_adequacy']=='unknown'

def test_plan_and_execution_are_distinct_even_with_positive_sets():
    raw=source('WK02-r1','workout.get_progress');out=module().project_evidence('workout.get_progress',raw)
    assert out['boundaries']['actual_relative_intensity']=='unknown'
    assert out['boundaries']['recorded_set_detail']=='absent'
    raw.update(total_sets=6,total_reps=60,total_volume_kg=1500)
    out=module().project_evidence('workout.get_progress',raw)
    assert out['boundaries']['recorded_set_detail']=='present'
    assert out['boundaries']['actual_relative_intensity']=='unknown'
    plan=module().project_evidence('plan.get_active',source('WK02-r1','plan.get_active'))
    prescribed=[f for f in plan['facts'] if f['kind']=='prescribed']
    completed=[f for f in plan['facts'] if f['kind']=='plan_linked_completed_days']
    assert prescribed and len(completed)==1
    assert len(prescribed)+len(completed)==len(plan['facts'])
    assert completed[0]['values']=={'weekly_completed_days':plan['business_data']['plan']['weekly_completed_days']}
    assert completed[0]['sources']=={'weekly_completed_days':['#/plan/weekly_completed_days']}
    assert all('weekly_completed_days' not in f['values'] for f in prescribed)
    assert plan['boundaries']['plan_completed_days_is_due_days'] is False
    assert plan['boundaries']['prescription_is_execution'] is False

def test_profile_missing_and_capability_contract():
    from app.schemas.profile import ProfileUpdateRequest
    assert 'nutrition_targets' not in ProfileUpdateRequest.model_fields
    out=module().project_evidence('profile.get_summary',{'found':False})
    assert out['facts']==[] and out['boundaries']['numeric_nutrition_goal_storage_supported'] is False

@pytest.mark.parametrize('value',[None,float('nan'),float('inf'),True,'31'])
def test_invalid_numbers_are_not_cast_to_facts(value):
    raw=source('NA11-r2','nutrition.list_history');raw['days'][0]['total_protein_g']=value
    out=module().project_evidence('nutrition.list_history',raw)
    assert all('protein_g' not in f['values'] for f in out['facts'] if f['kind']=='recorded_day')

@pytest.mark.asyncio
async def test_middleware_preserves_unrelated_failed_and_history_messages():
    middle=module().AssessmentGroundingMiddleware()
    messages=[HumanMessage(content='记住我的限制'),ToolMessage(content='{"found":true}',tool_call_id='x',name='profile_get_summary',status='error'),ToolMessage(content='private',tool_call_id='y',name='unknown_tool')]
    request=SimpleNamespace(messages=messages,system_message=SystemMessage(content='base'))
    request.override=lambda **kw:SimpleNamespace(**kw)
    handler=AsyncMock(return_value='ok')
    assert await middle.awrap_model_call(request,handler)=='ok'
    assert handler.call_args.args[0].messages==messages
    assert all('assessment_evidence_version' not in m.content for m in messages)

def test_unknown_or_malformed_tool_data_is_not_promoted():
    assert module().project_evidence('unregistered',{'found':True}) is None
    assert module().project_evidence('nutrition.list_history',['bad']) is None
