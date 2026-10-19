"""Real R58 routes are replayed without external model requests."""
import copy,json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from app.services import agent_intent_model as routing
from app.services.agent_intent_model import IntentRouteDecision,resolve_intent_with_fallback
from app.services.history_status_scope import parse_history_status_scope,history_status_problem

from evals.read_task_score import score_read_task
from functools import partial
score=partial(score_read_task,business_date='2026-10-09')
ROOT=Path(__file__).resolve().parent/'fixtures/round59'

def archived(id):
    return json.loads((ROOT/(id+'.json')).read_text(encoding='utf8'))

def routes(run):
    return [json.loads(t['function']['arguments']) for p in run['provider']
            for c in p.get('response',{}).get('choices',[]) for t in c.get('message',{}).get('tool_calls',[])
            if t['function']['name']=='submit_semantic_route']

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['WX24-r1','WX24-r2'])
async def test_baseline_saved_history_and_new_symptom_are_advice_not_write(id,monkeypatch):
    r=archived(id);message=r['turns'][0]['message']
    monkeypatch.setattr(routing.settings,'DEEPSEEK_API_KEY','synthetic-offline')
    for raw in routes(r):
        monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
        result=await resolve_intent_with_fallback(message,use_model=True)
        assert not result.understanding_failed
        assert result.resolution.requested_effect=='read' and not result.resolution.change_requests
        assert result.resolution.risk_level=='medium'
        assert '膝' in result.resolution.resolved_query
        assert result.resolution.read_scope_quotes
        assert all(q in message for q in result.resolution.read_scope_quotes)
        from app.services.agent_intent import required_read_tools
        assert {'plan.get_active','health.get_screening_summary'} <= set(required_read_tools(result.resolution))

@pytest.mark.asyncio
@pytest.mark.parametrize('id',['CW15-r1','CW15-r2'])
async def test_baseline_explicit_completed_counter_needs_no_status_clarification(id,monkeypatch):
    r=archived(id);message=r['turns'][0]['message']
    monkeypatch.setattr(routing.settings,'DEEPSEEK_API_KEY','synthetic-offline')
    scope=parse_history_status_scope(message)
    assert scope.complete and scope.statuses==('completed',)
    for raw in routes(r):
        monkeypatch.setattr(routing,'_invoke_model_route',AsyncMock(return_value=IntentRouteDecision.model_validate(raw)))
        result=await resolve_intent_with_fallback(message,use_model=True)
        assert not result.understanding_failed
        assert not history_status_problem(result.resolution,message)

def test_baseline_dispatcher_failure_is_counted_in_addition_to_function_entries():
    r=archived('CW10-r2')
    # Original function-only metric misses one real native dispatch failure.
    probe={'id':'probe','expected_tools':['plan.get_active'],'requires_nutrition':False}
    assert 'tool_error' in score(probe,r)['errors']

@pytest.mark.parametrize('question',[
    '请告诉我现在应该怎么调整训练。',
    '工作很累，请解释如何修改训练计划，暂时不保存。',
    '我想知道怎样把卧推组数改为两组，只讲步骤。',
    '请告诉我如何根据已有健康资料调整每次时长。',
    '想问你如何记录体重，而不是帮我写入。',
    '请说明如何把当前计划中的卧推重量改为20公斤。',
    '档案没有伤病记录，但今天踝关节不适。请告诉我怎么调整，别保存。',
])
def test_polite_embedded_question_is_not_write(question):
    from app.services.agent_intent import resolve_intent
    r=resolve_intent(question)
    assert r.requested_effect=='read' and r.request_kind!='mutation'

@pytest.mark.parametrize('message',[
    '请把卧推改为4组，可以吗？',
    '请告诉我怎么调整训练，然后把卧推改为4组。',
    '请告诉我怎么训练，并记录体重72公斤。',
    '请解释如何调整，同时保存体重72公斤。',
    '请说明如何训练。另外在档案里添加膝部疼痛。',
    '请告诉我怎么调整，再帮我把卧推休息改成120秒。',
    '请把膝部疼痛记录到健康档案。',
    '不是不让你记录体重72公斤，请现在记录。',
    '不是要询问如何修改，请把卧推改为4组。',
])
def test_independent_positive_writes_are_preserved(message):
    from app.services.agent_intent import resolve_intent
    r=resolve_intent(message)
    assert r.request_kind=='mutation' and r.requested_effect!='read'

@pytest.mark.parametrize('suffix',[
    '不要把进行中当已完成', '别把进行中算作已完成',
    '不要把提前结束当作已完成', '跳过、进行中都不要算作已完成',
    '也别把完成数为零说成没有任何训练记录',
    '不要把跳过和放弃算作已完成',
    '不要把提前结束和进行中计入已完成',
])
def test_completed_counter_explanations_have_one_shared_status_scope(suffix):
    message='请核对本周到今天已完成训练场次和计划完成日。'+suffix+'。'
    r=parse_history_status_scope(message)
    assert r.complete and r.statuses==('completed',)

@pytest.mark.parametrize('message',[
    '查本周不是不排除进行中的训练',
    '查本周未完成的训练记录',
    '查本周训练，已完成也不计为已完成',
    '查本周已完成，但不要排除进行中',
])
def test_ambiguous_or_double_negative_status_is_not_invented(message):
    assert not parse_history_status_scope(message).complete

def test_real_dispatcher_rejection_is_reconciled_without_inventing_invocation():
    from evals.tool_attempt_ledger import tool_attempt_ledger
    r=archived('CW10-r2');s=tool_attempt_ledger(r)
    assert s['complete'],s['issues']
    assert (s['attempts'],s['function_invocations'],s['successes'],s['errors'],s['dispatcher_rejections'])==(4,3,3,1,1)
    assert s['error_rate']==.25
    assert len([x for x in s['rows'] if x['origin']=='dispatcher_rejection' and not x['function_attempt_id']])==1

@pytest.mark.parametrize('change',[
    'missing_audit','missing_observation','missing_function','missing_native_call',
    'missing_error_message','offered_tool','duplicate_call','duplicate_function_id',
    'duplicate_audit','wrong_args','wrong_result','wrong_status','unfinished_function',
    'orphan_observation','tool_message_without_trace',
])
def test_incomplete_or_fabricated_ledger_never_reports_perfect_success(change):
    from evals.tool_attempt_ledger import tool_attempt_ledger
    r=archived('CW10-r2');trace=r['turns'][0]['body']['execution_trace'];bad=next(x for x in trace['actions'] if x['status']=='failed');cid=bad['call_id']
    if change=='missing_audit':r['tool_audits'].pop()
    elif change=='missing_observation':trace['observations'].pop()
    elif change=='missing_function':r['tool_attempts'].pop()
    elif change=='missing_native_call':
        for pr in r['provider']:
            for ch in pr['response'].get('choices',[]):
                ch['message']['tool_calls']=[c for c in ch['message'].get('tool_calls',[]) if c['id']!=cid]
    elif change=='missing_error_message':
        for pr in r['provider']:pr['request']['messages']=[m for m in pr['request']['messages'] if m.get('tool_call_id')!=cid]
    elif change=='offered_tool':
        for pr in r['provider']:pr['request'].setdefault('tools',[]).append({'function':{'name':'workout_list_history'}})
    elif change=='duplicate_call':trace['actions'].append(copy.deepcopy(trace['actions'][0]))
    elif change=='duplicate_function_id':r['tool_attempts'][1]['attempt_id']=r['tool_attempts'][0]['attempt_id']
    elif change=='duplicate_audit':r['tool_audits'].append(copy.deepcopy(r['tool_audits'][0]))
    elif change=='wrong_args':r['tool_audits'][0]['arguments_data']={'other':1}
    elif change=='wrong_result':r['tool_audits'][0]['result_data']={'invented':True}
    elif change=='wrong_status':trace['actions'][0]['status']='failed'
    elif change=='unfinished_function':r['tool_attempts'][0]['lifecycle_complete']=False
    elif change=='orphan_observation':trace['observations'].append({'call_id':'orphan'})
    else:trace['actions'].pop();trace['observations'].pop();r['tool_audits'].pop();r['tool_attempts'].pop()
    result=tool_attempt_ledger(r)
    assert not result['complete'] and result['issues']
    assert result['success_rate'] is None and result['error_rate'] is None

def test_no_tool_calls_have_no_success_or_error_rate():
    from evals.tool_attempt_ledger import tool_attempt_ledger
    r=tool_attempt_ledger({'turns':[{'body':{'execution_trace':{'actions':[],'observations':[]}}}]})
    assert r['complete'] and r['attempts']==0 and r['success_rate'] is None and r['error_rate'] is None

def test_recovered_task_keeps_tool_error_and_requires_business_review():
    r=archived('CW10-r2');probe={'id':'probe','expected_tools':['plan.get_active','workout.list_history'],'requires_nutrition':False}
    s=score(probe,r)
    assert not s['structural_pass'] and 'tool_error' in s['errors'] and s['terminal_delivery_eligible']
    assert s['semantic_pass'] is None and s['tool_ledger']['errors']==1

@pytest.mark.parametrize('message,risk,kind,effect',[
    ('帮我保存这份临时训练方案','medium','mutation','update'),
    ('突然胸痛，请给临时训练建议','high','query','read'),
    ('请给膝痛的临时治疗方案','medium','query','read'),
    ('不要提供临时训练建议，只核对健康档案','medium','query','read'),
])
def test_health_training_prefetch_does_not_expand_other_requests(message,risk,kind,effect):
    from app.services.agent_task_evidence import task_read_evidence
    required,optional,quotes=task_read_evidence(message,domain='health',kind=kind,effect=effect,risk=risk,evidence=['health_screening'])
    assert required==['health_screening'] and not quotes
