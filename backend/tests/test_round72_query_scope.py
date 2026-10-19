"""Archived failures, source scope counterexamples; no model/network calls."""
import copy,json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.services.agent_count_evidence import _count_scope
from app.services.agent_training_delivery import delivery_request
from app.services.agent_query_scope_evidence import nutrition_date_coverage,workout_count_coverage
from app.services.assessment_grounding import project_evidence
from app.services.history_status_scope import history_status_problem
from app.services.business_clock import BUSINESS_DATE
from app.services.agent_task_evidence import task_read_evidence
from test_round56_evidence_completion import resolution,tools
from app.services.agent_required_evidence import prepare_required_evidence
from app.services.agent_intent import route_tools
from langchain_core.messages import ToolMessage
from app.schemas.agent_trace import AgentExecutionTrace
from app.services.agent_trace import observation_fingerprint
from app.services.agent_training_delivery import ground_training_delivery, _counter_delivery, DeliveryRequest

DATA=json.loads((Path(__file__).parent/'fixtures/round72/archived.json').read_text(encoding='utf8'))
TODAY=date(2026,10,9)


@pytest.fixture(autouse=True)
def clock():
    token=BUSINESS_DATE.set(TODAY)
    yield
    BUSINESS_DATE.reset(token)


@pytest.mark.parametrize('id',[k for k in DATA if k.startswith('CW')])
def test_original_counter_scope_and_complete_delivery_ownership(id):
    message=DATA[id]['message'];scope=_count_scope(message,TODAY)
    assert scope and scope[:2]==(date(2026,10,5),TODAY) and scope[3]==('completed',)
    request=delivery_request(message)
    assert request and request.mode=='training_counters',message
    assert request.intensity==(id in {'CW12-r1','CW14-r1'})


@pytest.mark.asyncio
@pytest.mark.parametrize('id',['CW12-r1','CW14-r1'])
@pytest.mark.parametrize('total',[0,7,25])
async def test_mixed_intensity_retains_exact_count_and_original_obligations(id,total):
    query=DATA[id]['message'];r=resolution(query);ts,calls=tools(total)
    result=await prepare_required_evidence(None,tools=ts,resolution=r,allowlist=route_tools(r),query=query)
    assert not result.error_code and not result.pending_tools
    history=next(args for name,args in calls if name=='workout.list_history')
    assert history['statuses']==['completed'] and history['start_date']=='2026-10-05'
    assert len(calls)==(3 if total>20 else 2)
    evidence,optional,anchors=task_read_evidence(query,domain='workout_progress',kind='assessment',effect='read',risk='low',evidence=r.evidence_requirements)
    assert set(evidence)-set(optional)=={'active_plan','workout_history','workout_progress'}
    assert any('强度' in q for q in anchors) and query==DATA[id]['message']


@pytest.mark.parametrize('tail',[
    '另外列出每场动作明细。','另外分析最近8周的趋势。','再告诉我饮食是否达标。',
    '如果强度合适就删除这些记录。','把计划改成每周五天。','比较上周与本周。',
    '不要只判断训练强度是否合适。','进行中的也要单独报数。',
])
def test_extra_goal_is_never_swallowed_by_full_answer_renderer(tail):
    assert delivery_request(DATA['CW12-r1']['message']+tail) is None


@pytest.mark.parametrize('text',[
    '如果本周已完成训练场次很多再统计计划完成日。',
    '请查询“本周已完成训练场次”这句话是什么意思。',
    '本周未完成训练场次和计划完成日是多少？',
    '本周已完成训练场次和计划完成日，平均强度与容量如何？',
])
def test_ambiguous_or_other_count_metrics_do_not_get_false_scope(text):
    assert _count_scope(text,TODAY) is None


def test_model_paraphrase_cannot_invent_missing_status_but_unknown_source_stays_unknown():
    query=DATA['CW15-r1']['message'];r=resolution(query)
    r.intent_domain='workout_history';r.resolved_query='查询本周未完成训练记录'
    assert not history_status_problem(r,query)
    r.read_scope_quotes=[]
    assert history_status_problem(r,query)
    query='核对本周未完成训练场次和计划完成日。';r=resolution(query)
    r.intent_domain='workout_history';r.resolved_query='本周已完成训练场次和计划完成日'
    assert history_status_problem(r,query)


@pytest.mark.parametrize('id',['CW17-r1','CW18-r2','CW19-r2','CW20-r1'])
def test_archived_actual_observations_replace_bad_definitions(id):
    r=copy.deepcopy(DATA[id]);trace=AgentExecutionTrace.model_validate(r['trace']);messages=[]
    for t in r['tools']:
        m=t.get('tool_message')
        if m is None:
            args=t['arguments'].get('args',t['arguments'])
            action=next(a for a in trace.actions if a.tool_id==t['tool'] and a.arguments==args)
            wire=next(w for w in r['provider_tool_messages'] if w['tool_call_id']==action.call_id)
            view=json.loads(wire['content']);assert view['tool']==t['tool']
            m={'name':t['tool'].replace('.','_'),'content':json.dumps(view['business_data'],ensure_ascii=False),'tool_call_id':action.call_id,'status':'success'}
        assert m['status']=='success'
        assert json.loads(m['content'])==t['result']
        observed=next(o for o in trace.observations if o.call_id==m['tool_call_id'])
        assert observed.result_fingerprint==observation_fingerprint(m['content'])
        messages.append(ToolMessage(**m))
    reply,trace=ground_training_delivery(r['reply'],trace,result={'messages':messages},query=r['message'],resolution=resolution(r['message']))
    assert trace.training_delivery and reply!=r['reply']
    assert '不要求关联当前计划' in reply and '不是每周安排几天或截至今天应练几天' in reply
    assert '某状态计数为0也不代表其他状态没有记录' in reply


@pytest.mark.parametrize('field,value',[('total_sets',3),('exercises',[{'name':'真实动作'}]),('feedback',{'rpe':8})])
def test_actual_effort_details_are_not_replaced_with_blanket_missing_answer(field,value):
    r=copy.deepcopy(DATA['CW17-r1'])
    records={t['tool']:(t['result'],t['arguments'].get('args',t['arguments']),'x','0'*64) for t in r['tools']}
    records['workout.list_history'][0]['sessions'][0][field]=value
    assert _counter_delivery(DATA['CW12-r1']['message'],DeliveryRequest('training_counters',intensity=True),records) is None


def nutrition(id='NX04-r1'):
    return copy.deepcopy(next(t['result'] for t in DATA[id]['tools'] if t['tool']=='nutrition.list_history'))


@pytest.mark.parametrize('id,empty',[('NX04-r1',['2026-10-09']),('NX06-r2',[]),('NA12-r1',['2026-10-09'])])
def test_archived_date_partition_records_empty_and_future(id,empty):
    data=nutrition(id);before=copy.deepcopy(data);proof=nutrition_date_coverage(data)
    assert proof['queried_dates']==['2026-10-08','2026-10-09']
    assert proof['queried_empty_dates']==empty
    assert proof['future_excluded_dates']==(['2026-10-10'] if id=='NA12-r1' else ['2026-10-10','2026-10-11'])
    assert proof['recorded_dates'][0]['meal_scope']=='all_stored_meals'
    assert proof['recorded_dates'][0]['actual_intake_completeness']=='unknown'
    assert project_evidence('nutrition.list_history',data)['boundaries']['date_coverage']==proof
    assert data==before


@pytest.mark.parametrize('mode',['meals','specific','latest','empty','future_only'])
def test_scope_variants_do_not_infer_unqueried_dates_or_meals(mode):
    data=nutrition();s=data['scope']
    if mode=='meals':s['meal_types']=['午餐']
    elif mode=='specific':s.update(selection='specific_dates',start_date=None,end_date=None,logged_dates=['2026-10-08','2026-10-11'])
    elif mode=='latest':s.update(selection='latest_logged_dates',start_date=None,end_date=None,requested_limit=30)
    elif mode=='empty':data['days']=[];s['returned_dates']=0
    elif mode=='future_only':s['start_date']='2026-10-10';data['days']=[];s['returned_dates']=0
    proof=nutrition_date_coverage(data);assert proof
    if mode=='meals':assert proof['recorded_dates'][0]['other_meals_record_presence']=='not_queried'
    if mode in {'specific','latest','future_only'}:assert proof['queried_empty_dates']==[]
    if mode=='latest':assert not proof['missing_calendar_dates_are_empty']
    if mode=='future_only':assert proof['queried_dates']==[]
    assert not proof['empty_record_is_zero_actual_intake']


@pytest.mark.parametrize('mode',['timezone','bad_date','reversed','too_long','duplicate','future_row','outside_row','bad_meals','wrong_meal','count','bad_selected','bad_specific'])
def test_malformed_history_metadata_has_no_scope_certificate(mode):
    data=nutrition();s=data['scope']
    if mode=='timezone':s['timezone']='UTC'
    elif mode=='bad_date':s['as_of']='20261009'
    elif mode=='reversed':s['start_date']='2026-10-12'
    elif mode=='too_long':s['start_date']='2024-10-08'
    elif mode=='duplicate':data['days'].append(copy.deepcopy(data['days'][0]));s['returned_dates']=2
    elif mode=='future_row':data['days'][0]['date']='2026-10-10'
    elif mode=='outside_row':data['days'][0]['date']='2026-10-07'
    elif mode=='bad_meals':data['days'][0]['meals']='invalid'
    elif mode=='wrong_meal':s['meal_types']=['早餐']
    elif mode=='count':s['returned_dates']=2
    elif mode=='bad_selected':s['meal_types']=[[]]
    else:s.update(selection='specific_dates',start_date=None,end_date=None,logged_dates=['2026-10-08']*2)
    assert nutrition_date_coverage(data) is None


@pytest.mark.parametrize('id',['CW12-r1','CW17-r1','CW19-r2'])
def test_counter_projection_keeps_unknown_status_and_load(id):
    for t in DATA[id]['tools']:
        if t['tool'] not in {'workout.list_history','workout.get_progress'}:continue
        before=copy.deepcopy(t['result']);proof=workout_count_coverage(t['tool'],before)
        assert proof and not proof['current_plan_link_required'] and proof['actual_load_not_proven_by_zero_sets']
        if id=='CW12-r1':assert proof['exact_status_counts'] is None
        else:
            assert 'in_progress' in proof['not_queried_statuses']
            assert proof['exact_status_counts']=={'completed':1 if id=='CW17-r1' else 0}
        assert before==t['result']
