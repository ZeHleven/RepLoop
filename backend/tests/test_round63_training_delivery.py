"""Retained R61 failures; assertions target final delivered source-bound values."""
import copy
import json
from pathlib import Path
import pytest
from langchain_core.messages import ToolMessage
from test_round58_grounded_delivery import grounded, changed_source, archived_messages
from app.services.agent_training_constraints import training_adjustment_context, training_schedule, adjustment_budget
from app.services.agent_intent import IntentResolution

ROWS=json.loads((Path(__file__).parent/'fixtures/round63/archived-delivery.json').read_text(encoding='utf-8'))['rows']
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))
def resolution(r):
    b=r['body']
    return IntentResolution.model_validate({**{k:v for k,v in b.items() if k in IntentResolution.model_fields},'confidence':b['intent_confidence']})


@pytest.mark.parametrize('id',['WK02-r1','WK02-r2'])
def test_no_explicit_time_still_receives_source_bound_weekly_limits(id):
    r=row(id);context=training_adjustment_context(r['message'],resolution(r),archived_messages(r))
    assert context, 'Fatigue request without numeric minutes must retain workload constraints'
    value=json.loads(context.splitlines()[-1]);spec=value['training_adjustment_envelope']
    assert spec['session_cap_minutes'] is None
    assert spec['temporary_weekly_sets']<spec['baseline_weekly_sets']==6
    assert value['limit_source']=='unspecified_by_user'


@pytest.mark.parametrize('id',['WK02-r1','WK02-r2','WX23-r1'])
def test_original_failed_final_response_replaced_with_checked_delivery(id):
    r=row(id);reply,trace=grounded(r)
    assert trace.training_delivery is not None, 'Final response must consume the verified schedule'
    delivery=trace.training_delivery
    assert delivery.version==2
    assert delivery.temporary_weekly_sets<delivery.baseline_weekly_sets
    assert reply!=r['reply']
    assert '不能保证' in reply and '没有保存' in reply
    if id.startswith('WX23'):
        assert delivery.temporary_weekly_sets==delivery.temporary_days==0
        assert delivery.session_cap_minutes==5
        assert '不安排原力量工作组' in reply
        assert '组×' not in reply and '第1次：' not in reply
        assert '时限' in reply and '放宽' in reply
    else:
        assert delivery.session_cap_minutes is None
        assert '没有给出每次的硬时限' in reply
        assert '每周1天' in reply and '约13分钟' in reply
        assert '90秒' in reply


@pytest.mark.parametrize('suffix',['另外分析最近8周训练趋势。','只练卧推，不做深蹲。','同时保存这个新计划。','必须每周练5天。','每次可能5分钟也可能15分钟。'])
def test_uncovered_goal_or_conflict_does_not_get_swallowed(suffix):
    r=row('WK02-r1');reply,trace=grounded(r,message=r['message']+suffix)
    assert trace.training_delivery is None and reply==r['reply']


@pytest.mark.parametrize('minutes',[None,1,5,6,15,60])
def test_shared_schedule_handles_tiny_prescription_and_zero_without_increasing_volume(minutes):
    data=next(t['result'] for t in row('WX23-r1')['tools'] if t['tool']=='plan.get_active')
    spec=adjustment_budget(training_schedule(data),minutes)
    assert spec and spec['session_cap_minutes']==minutes
    assert 0<=spec['temporary_weekly_sets']<2
    assert spec['temporary_days']<=1
    assert sum(e['sets'] for s in spec['sessions'] for e in s['exercises'])==spec['temporary_weekly_sets']
    assert all(s['estimated_seconds']==240+sum(e['sets']*(e['reps']*4+e['rest_seconds']) for e in s['exercises']) for s in spec['sessions'])
    if minutes is not None:assert all(s['estimated_seconds']<=minutes*60 for s in spec['sessions'])


@pytest.mark.parametrize('variant',['no_health','incomplete_health','current_symptom','bad_fingerprint','unknown_reps','write'])
def test_final_delivery_does_not_bypass_evidence_or_authority(variant):
    r=row('WK02-r1')
    if variant=='no_health':changed_source(r,'health.get_screening_summary',lambda d:d.update(found=False))
    if variant=='incomplete_health':changed_source(r,'health.get_screening_summary',lambda d:d.update(screening_completed=False))
    if variant=='unknown_reps':changed_source(r,'plan.get_active',lambda d:d['plan']['exercises'][0].update(reps='AMRAP'))
    def mutate(trace,result,res):
        if variant=='bad_fingerprint':result['messages'][0].content='{}'
        if variant=='write':res.request_kind='mutation';res.requested_effect='update'
    message=r['message']+('我现在走路也膝盖痛。' if variant=='current_symptom' else '')
    reply,trace=grounded(r,message=message,mutate=mutate)
    assert trace.training_delivery is None and reply==r['reply']


@pytest.mark.parametrize('change',['missing_provenance','invented_cap','zero_sets_nonzero_days','nonzero_sets_zero_days','missing_timing','over_time','wrong_empty_status','legacy_without_cap'])
def test_delivery_receipt_rejects_inconsistent_numeric_contract(change):
    from app.schemas.agent_trace import AgentTrainingDeliveryTrace
    from pydantic import ValidationError
    value=dict(version=2,mode='temporary_training',source_call_ids=['call'],source_fingerprints=['fingerprint'],
               baseline_weekly_sets=6,temporary_weekly_sets=4,temporary_days=1,duration_days=7,
               session_cap_minutes=None,session_cap_source='unspecified',estimated_session_seconds=[728],
               allocation_status='conditional_reduced_schedule')
    assert AgentTrainingDeliveryTrace.model_validate(value).temporary_weekly_sets==4
    if change=='missing_provenance':value.pop('session_cap_source')
    elif change=='invented_cap':value['session_cap_minutes']=15
    elif change=='zero_sets_nonzero_days':value['temporary_weekly_sets']=0
    elif change=='nonzero_sets_zero_days':value['temporary_days']=0;value['estimated_session_seconds']=[]
    elif change=='missing_timing':value.pop('estimated_session_seconds')
    elif change=='over_time':value.update(session_cap_minutes=5,session_cap_source='user')
    elif change=='wrong_empty_status':value['allocation_status']='no_work_sets_fit'
    else:value['version']=1
    with pytest.raises(ValidationError):AgentTrainingDeliveryTrace.model_validate(value)


@pytest.mark.parametrize('minutes',[None,15])
def test_zero_for_one_original_set_explains_reduction_instead_of_inventing_time_shortage(minutes):
    r=row('WK02-r1')
    def one_set(d):
        d['plan']['exercises']=d['plan']['exercises'][:1]
        d['plan']['exercises'][0]['sets']=1
    changed_source(r,'plan.get_active',one_set)
    message=r['message']+(f'每次最多{minutes}分钟。' if minutes else '')
    reply,trace=grounded(r,message=message)
    assert trace.training_delivery and trace.training_delivery.temporary_weekly_sets==0
    assert '每周只有1组' in reply and '严格减少周组数' in reply
    assert '当前时限不容纳' not in reply
    assert '调整减量目标' in reply
