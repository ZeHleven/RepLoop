"""R57 failures retained as contract probes, with no external model calls."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import ToolMessage
from app.services import agent_intent_model as routing
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.services.agent_intent import required_read_tools
from app.services.agent_count_evidence import _count_scope
from app.services.agent_tools import WorkoutProgressArguments

ROWS=json.loads((Path(__file__).parent/'fixtures/round58/archived-delivery.json').read_text(encoding='utf8'))['rows']
def row(id):return copy.deepcopy(next(r for r in ROWS if r['id']==id))

@pytest.mark.parametrize('id,index',[(r['id'],i) for r in ROWS for i,t in enumerate(r['tools']) if t['status']=='error'])
def test_baseline_actual_calendar_parameters_have_one_clear_scope(id,index):
    t=row(id)['tools'][index];args=t['arguments']['args']
    # Explicit dates are authoritative; week defaults must not change them.
    value=WorkoutProgressArguments.model_validate(args)
    assert (value.start_date,value.end_date,value.statuses)==(args['start_date'],args['end_date'],args['statuses'])

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['CW10-r1','CW10-r2','CW13-r1','CW13-r2'])
async def test_baseline_count_nouns_and_zero_explanation_keep_obligations(id,monkeypatch):
    monkeypatch.setattr(routing.settings,'DEEPSEEK_API_KEY','synthetic-offline')
    r=row(id)
    for raw in r['routes']:
        monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
        outcome=await resolve_intent_with_fallback(r['message'],use_model=True)
        assert not outcome.understanding_failed
        assert set(required_read_tools(outcome.resolution))=={'plan.get_active','workout.list_history','workout.get_progress'}
    scope=_count_scope(r['message'],date(2026,10,9))
    assert scope and scope[:2]==(date(2026,10,5),date(2026,10,9)) and scope[3]==('completed',)


def archived_messages(r):
    from app.services.agent_trace import observation_fingerprint
    messages=[]
    for tool in r['tools']:
        if tool['status'] != 'success':continue
        args=tool['arguments'].get('args', tool['arguments'])
        actions=[a for a in r['trace']['actions'] if a['tool_id']==tool['tool'] and a['arguments']==args]
        assert len(actions)==1
        action=actions[0];content=json.dumps(tool['result'],ensure_ascii=False)
        observed=next(o for o in r['trace']['observations'] if o['call_id']==action['call_id'])
        assert observation_fingerprint(content)==observed['result_fingerprint']
        messages.append(ToolMessage(content=content,name=tool['tool'].replace('.','_'),tool_call_id=action['call_id'],status='success'))
    return messages


def grounded(r, *, message=None, mutate=None):
    from app.services.agent_intent import IntentResolution
    from app.schemas.agent_trace import AgentExecutionTrace
    from app.services.agent_training_delivery import ground_training_delivery
    from app.services.business_clock import BUSINESS_DATE
    token=BUSINESS_DATE.set(date(2026,10,9))
    try:
        trace=AgentExecutionTrace.model_validate(r['trace']);result={'messages':archived_messages(r)}
        resolution=IntentResolution(primary_intent='plan_query',intent_domain='workout_plan',request_kind='query',requested_effect='read',risk_level='low',confidence=.99)
        if mutate:mutate(trace,result,resolution)
        return ground_training_delivery(r['reply'],trace,result=result,query=message or r['message'],resolution=resolution)
    finally:BUSINESS_DATE.reset(token)


@pytest.mark.parametrize('id',[r['id'] for r in ROWS if r['id'].startswith('WX')])
def test_archived_temporary_delivery_reduces_weekly_load_with_sources(id):
    r=row(id);reply,trace=grounded(r)
    assert trace.training_delivery is not None,id
    receipt=trace.training_delivery
    assert receipt.temporary_weekly_sets < receipt.baseline_weekly_sets
    assert receipt.temporary_days <= next(t['result']['plan']['days_per_week'] for t in r['tools'] if t['tool']=='plan.get_active')
    assert '余力' in reply and '不规定第几次' in reply and '停止' in reply and '没有保存' in reply
    if '体重' in r['message']:assert '72 kg' in reply
    assert all(call in {o['call_id'] for o in r['trace']['observations']} for call in receipt.source_call_ids)
    assert r['reply']==row(id)['reply'],'Original model answer must remain unchanged'


@pytest.mark.parametrize('id',['CW04-r2','CW05-r1','CW08-r1','CW08-r2','CW09-r1','CW09-r2','CW12-r1','CW14-r1','CW14-r2'])
def test_archived_count_delivery_keeps_completed_distinct_from_scheduled(id):
    reply,trace=grounded(row(id));receipt=trace.training_delivery
    assert receipt and receipt.mode=='training_counters'
    assert '不是每周安排几天' in reply
    assert ('没有已记录场次' not in reply and '集中在' not in reply)
    if id.startswith(('CW08','CW12','CW14')):assert receipt.history_session_count==25 and receipt.plan_completed_days==0 and '列表被截断' in reply


@pytest.mark.parametrize('suffix',[
    '另外讲一个笑话。','同时删除昨天记录。','再告诉我午餐蛋白是否达标。',
    '必须每周练5天。','只练卧推，不做深蹲。','把重量增加5公斤。',
    '一周每天不超过15分钟，两周每次30分钟。','再分析最近训练的趋势。',
])
def test_mixed_or_unrecognized_goals_are_not_swallowed(suffix):
    r=row('WX18-r2');reply,trace=grounded(r,message=r['message']+suffix)
    assert trace.training_delivery is None and reply==r['reply']


@pytest.mark.parametrize('change',['fingerprint','error','partial','write','high','stale','clarification'])
def test_no_unverified_delivery_or_permission_bypass(change):
    def mutate(trace,result,resolution):
        if change=='fingerprint':result['messages'][0].content='{"found":true}'
        elif change=='error':result['read_error_code']='read_tool_timeout'
        elif change=='partial':trace.evidence_contract.status='partial'
        elif change=='write':resolution.requested_effect='write'
        elif change=='high':trace.risk_level='high'
        elif change=='stale':trace.request_context.as_of='2026-10-08'
        else:resolution.clarification_required=True
    r=row('WX18-r2');reply,trace=grounded(r,mutate=mutate)
    assert trace.training_delivery is None and reply==r['reply']


def changed_source(r, tool_id, edit):
    """Counterfactual successful tool data; originals remain archived intact."""
    from app.services.agent_trace import observation_fingerprint
    tool=next(t for t in r['tools'] if t['tool']==tool_id)
    edit(tool['result'])
    call=next(a['call_id'] for a in r['trace']['actions'] if a['tool_id']==tool_id)
    observed=next(o for o in r['trace']['observations'] if o['call_id']==call)
    observed['result_fingerprint']=observation_fingerprint(json.dumps(tool['result'],ensure_ascii=False))


@pytest.mark.parametrize('days,sets,reps,rest,minutes',[
    (1,2,'8',30,6),(1,4,'8-12',120,10),(2,3,'10',90,15),
    (3,4,'6',180,12),(4,5,'12',120,20),(5,3,'15',60,15),
    (6,4,'8',90,30),(7,10,'30',300,60),
])
def test_varied_prescriptions_respect_weekly_arithmetic_and_time(days,sets,reps,rest,minutes):
    r=row('WX18-r2')
    def edit(data):
        plan=data['plan'];original=copy.deepcopy(plan['exercises']);plan['days_per_week']=days;plan['exercises']=[]
        for day in range(1,days+1):
            for i,ex in enumerate(original):
                ex=copy.deepcopy(ex);ex.update(id=f'variant-{day}-{i}',day_of_week=day,sets=sets,reps=reps,rest_seconds=rest)
                plan['exercises'].append(ex)
    changed_source(r,'plan.get_active',edit)
    message=f'依据已保存计划和健康资料，给两周临时安排，每次最多{minutes}分钟，状态差可休息，恢复后怎么回到原计划？不保存。'
    reply,trace=grounded(r,message=message);receipt=trace.training_delivery
    assert receipt and receipt.baseline_weekly_sets==days*2*sets
    assert 0<receipt.temporary_weekly_sets<receipt.baseline_weekly_sets
    assert receipt.temporary_days==min(days,2) and receipt.duration_days==14
    assert receipt.session_cap_minutes==minutes and '到上限即结束' in reply
    import re
    sessions=re.split(r'\n第\d+次：',reply)[1:1+receipt.temporary_days]
    actual=0
    for session in sessions:
        prescribed=re.findall(r'：([0-9]+)组×([0-9]+)次，组间休息([0-9]+)秒',session)
        assert prescribed
        assert 240+sum(int(s)*(int(n)*4+int(rest)) for s,n,rest in prescribed)<=minutes*60
        actual+=sum(int(s) for s,_,_ in prescribed)
    assert actual==receipt.temporary_weekly_sets
    if days>2:assert f'最多恢复到每7天{days}次' in reply


@pytest.mark.parametrize('variant',[
    'no_plan','inactive','conflicting_days','unknown_reps','missing_rest','tiny',
    'no_health','incomplete_screening','chronic','unmatched_symptom','unsafe_plan','exercise_warning','too_short',
])
def test_unsupported_or_conflicting_sources_keep_original_path(variant):
    r=row('WX18-r2');message=r['message']
    def plan_edit(data):
        p=data['plan']
        if variant=='no_plan':data['found']=False
        elif variant=='inactive':p['is_active']=False
        elif variant=='conflicting_days':p['days_per_week']=3
        elif variant=='unknown_reps':p['exercises'][0]['reps']='AMRAP'
        elif variant=='missing_rest':p['exercises'][0]['rest_seconds']=None
        elif variant=='tiny':
            for ex in p['exercises']:ex['sets']=1
        elif variant=='unsafe_plan':p['safety_status']='incompatible'
        elif variant=='exercise_warning':p['exercises'][0]['safety_notice']='需重新评估'
    def health_edit(data):
        if variant=='no_health':data['found']=False
        elif variant=='incomplete_screening':data['screening_completed']=False
        elif variant=='chronic':data['chronic_conditions']=['合成限制']
    changed_source(r,'plan.get_active',plan_edit);changed_source(r,'health.get_screening_summary',health_edit)
    if variant=='unmatched_symptom':message+='膝部疼痛。'
    if variant=='too_short':message=message.replace('15分钟','5分钟')
    reply,trace=grounded(r,message=message)
    if variant in {'tiny','too_short'}:
        # R63 explicitly supports zero/partial allocations; old source and
        # expected fallback are archived in R63/source-before, not erased.
        receipt=trace.training_delivery
        assert receipt and receipt.version==2
        assert 0<=receipt.temporary_weekly_sets<receipt.baseline_weekly_sets
        assert receipt.session_cap_minutes==(5 if variant=='too_short' else 15)
        assert all(s<=receipt.session_cap_minutes*60 for s in receipt.estimated_session_seconds)
        if not receipt.temporary_weekly_sets:assert receipt.temporary_days==0 and '不安排原力量工作组' in reply
        return
    assert trace.training_delivery is None and reply==r['reply']


@pytest.mark.parametrize('id',['CW10-r1','CW10-r2','CW13-r1','CW13-r2'])
def test_zero_count_explanation_is_fully_recognized(id):
    from app.services.agent_training_delivery import delivery_request
    assert delivery_request(row(id)['message']).mode=='training_counters'

