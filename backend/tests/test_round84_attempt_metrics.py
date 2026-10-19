import copy,json,math
from pathlib import Path
import pytest
from evals.attempt_metrics import summarize_attempts,summarize_batch,InvalidObservation
from evals.attempt_capture import record_attempt_snapshot

FIXTURE=Path(__file__).parent/'fixtures/round84'
RECORDS={r['case_id']:r for r in json.loads((FIXTURE/'records.json').read_text(encoding='utf8'))}
GOLD=json.loads((FIXTURE/'gold.json').read_text(encoding='utf8'))
PRICES={'currency':'CNY','provenance':'synthetic fixture only, not current provider price',
        'model_by_source':{'agent':'synthetic'},'rates_by_model':{'synthetic':{'input_per_million':'2','cached_input_per_million':'0.04','output_per_million':'8'}}}

@pytest.fixture(autouse=True)
def setup_db():yield

def baseline():return copy.deepcopy(RECORDS['before_commit-1'])

@pytest.mark.parametrize('gold',GOLD,ids=lambda g:g['case_id'])
def test_frozen_actual_crash_records(gold):
    record=copy.deepcopy(RECORDS[gold['case_id']]);before=copy.deepcopy(record)
    result=summarize_attempts(record)
    assert record==before and result['evidence_valid'] and result['coverage_complete'],result
    assert result['model']['observed_requests']==gold['requests']
    assert result['model']['known_observations']['known_input_tokens']==gold['known_input_tokens']
    assert result['model']['known_observations']['known_output_tokens']==gold['known_output_tokens']
    assert result['model']['usage_complete']==gold['token_complete']
    assert result['model']['input_tokens']==(gold['known_input_tokens'] if gold['token_complete'] else None)
    tools=result['registered_read_intents']
    assert (tools['successes'],tools['pending'],tools['errors'])==(gold['successes'],gold['pending'],0)
    assert tools['success_rate']==gold['registered_rate']
    assert result['tool_dispatch']['success_rate'] is None
    assert result['cost']['total'] is None and result['business_score'] is None
    assert result['answer_accuracy'] is result['hallucination_rate'] is result['human_takeover_rate'] is None

MUTATIONS=['duplicate_attempt','foreign_attempt','missing_attempt_claim','duplicate_tool','bad_tool_json','bad_arguments','false_complete','fake_zero','bool_counter','bool_attempt','wrong_version','bad_http','wrong_http_outcome','wrong_last_response','bad_cache','bool_cached_convenience','unknown_tool_state','pending_with_result','negative_duration','conflicting_capture','foreign_capture','sql_wrong_scope','sql_wrong_usage','sql_not_readonly']
@pytest.mark.parametrize('mutation',MUTATIONS)
def test_corrupt_evidence_cannot_produce_complete_metrics(mutation):
    r=baseline();o=r['attempt_observations'][0];h=o['history'];a=h['attempts'][0];u=a['model_usage'];c=u['calls'][0];t=a['tool_calls'][0]
    if mutation=='duplicate_attempt':h['attempts'].append(copy.deepcopy(a))
    elif mutation=='foreign_attempt':a['worker_attempt']=3
    elif mutation=='missing_attempt_claim':h['missing_attempts']=[1]
    elif mutation=='duplicate_tool':a['tool_calls'].append(copy.deepcopy(t))
    elif mutation=='bad_tool_json':t['result_json']='{"value":NaN}'
    elif mutation=='bad_arguments':t['arguments_json']='[]'
    elif mutation=='false_complete':
        h['usage_complete']=False;h['input_tokens']=None;h['output_tokens']=None
    elif mutation=='fake_zero':u['http_requests']=0
    elif mutation=='bool_counter':u['http_errors']=False
    elif mutation=='bool_attempt':r['runs'][0]['attempt_count']=True
    elif mutation=='wrong_version':u['version']='v999'
    elif mutation=='bad_http':c['responses'][0]['http_status']=999
    elif mutation=='wrong_http_outcome':c['outcome']='http_error'
    elif mutation=='wrong_last_response':c['output_tokens']=999
    elif mutation=='bad_cache':c['responses'][0]['cached_input_tokens']=999
    elif mutation=='bool_cached_convenience':c['responses'][0]['cached_input_tokens']=0;c['cached_input_tokens']=False
    elif mutation=='unknown_tool_state':t['state']='ok'
    elif mutation=='pending_with_result':t['state']='pending'
    elif mutation=='negative_duration':t['duration_ms']=-1
    elif mutation=='conflicting_capture':
        other=copy.deepcopy(o);other['history']['attempts'].pop();r['attempt_observations'].append(other)
    elif mutation=='foreign_capture':o['run_id']='foreign'
    elif mutation.startswith('sql_'):
        r.update(user_id='owner',conversation_id='conversation')
        o['sql']={'run_id':o['run_id'],'user_id':'owner','conversation_id':'conversation','run_status':'completed','attempt_count':2,
                  'read_only':True,'isolation':'repeatable read','attempts':[{**copy.deepcopy(a),'run_id':o['run_id']} for a in h['attempts']]}
        if mutation=='sql_wrong_scope':o['sql']['user_id']='foreign'
        if mutation=='sql_wrong_usage':o['sql']['attempts'][0]['model_usage']['http_requests']=999
        if mutation=='sql_not_readonly':o['sql']['read_only']=False
    out=summarize_attempts(r)
    assert not out['evidence_valid'] and not out['coverage_complete'],(mutation,out)
    assert out['model']['input_tokens'] is None and out['tool_dispatch']['success_rate'] is None

def test_missing_history_is_unknown_and_partial_known_values_survive():
    r=baseline();h=r['attempt_observations'][0]['history'];h['attempts']=h['attempts'][1:];h['missing_attempts']=[1]
    h.update(usage_complete=False,input_tokens=None,output_tokens=None,known_input_tokens=24,known_output_tokens=10)
    result=summarize_attempts(r)
    assert result['evidence_valid'] and not result['coverage_complete']
    assert result['model']['known_observations']['known_input_tokens']==24 and result['model']['input_tokens'] is None
    assert result['registered_read_intents']['success_rate'] is None

def test_returned_error_is_failure_but_nested_business_error_is_not():
    r=baseline();calls=r['attempt_observations'][0]['history']['attempts'][0]['tool_calls']
    calls[0]['result_json']='{"ok":false}';calls[1]['result_json']='{"rows":[{"error":"historic"}]}'
    result=summarize_attempts(r)['registered_read_intents']
    assert result['errors']==result['returned_errors']==1 and result['successes']==3
    assert result['error_rate']==.25

def test_failed_and_cancelled_invocations_are_retained():
    r=baseline();calls=r['attempt_observations'][0]['history']['attempts'][0]['tool_calls']
    for call,state,error in zip(calls,['failed','cancelled'],['TimeoutError','CancelledError']):
        call.update(state=state,error_type=error,result_json=None)
    result=summarize_attempts(r)['registered_read_intents']
    assert result['errors']==2 and result['cancelled']==1 and result['error_rate']==.5

def test_repeat_capture_is_not_a_second_execution():
    r=baseline();first=summarize_attempts(r)
    r['attempt_observations'].append(copy.deepcopy(r['attempt_observations'][0]))
    assert summarize_attempts(r)==first
    r=baseline();o=copy.deepcopy(r['attempt_observations'][0]);record_attempt_snapshot(r,o);record_attempt_snapshot(r,o)
    assert len(r['attempt_observations'])==1 and len(r['attempt_observation_events'])==2
    assert summarize_attempts(r)==first

def test_multi_run_keeps_same_attempt_sequences_separate():
    a=baseline();b=copy.deepcopy(RECORDS['model_done-1'])
    a['runs']+=b['runs'];a['attempt_observations']+=b['attempt_observations']
    result=summarize_attempts(a)
    assert result['evidence_valid'] and result['model']['observed_requests']==7
    assert result['registered_read_intents']['observed']==6

def test_unknown_cache_produces_price_interval_not_fake_exact_cost():
    result=summarize_attempts(baseline(),prices=PRICES)
    assert result['cost']['total'] is None
    assert result['cost']['total_min']=='0.00016192' and result['cost']['total_max']=='0.000256'
    r=baseline()
    for a in r['attempt_observations'][0]['history']['attempts']:
        for c in a['model_usage']['calls']:
            c['cached_input_tokens']=0
            for response in c['responses']:response['cached_input_tokens']=0
    assert summarize_attempts(r,prices=PRICES)['cost']['total']=='0.000256'

@pytest.mark.parametrize('change',['missing','unbound','negative','nonfinite','float','currency'])
def test_missing_or_invalid_price_cannot_be_full_cost(change):
    p=copy.deepcopy(PRICES)
    if change=='missing':p.pop('provenance')
    if change=='unbound':p['model_by_source']={}
    if change in ('negative','nonfinite','float'):p['rates_by_model']['synthetic']['input_per_million']={'negative':'-1','nonfinite':'NaN','float':2.0}[change]
    if change=='currency':p['currency']='USD'
    result=summarize_attempts(baseline(),prices=p)
    assert result['cost']['total_min'] is None and result['cost']['total_max'] is None

@pytest.mark.parametrize('value,valid,scope,expected',[(123,True,'submission_to_terminal',123),(123,False,'submission_to_terminal',None),(123,True,None,None),(float('nan'),True,'submission_to_terminal',None),(True,True,'submission_to_terminal',None)])
def test_latency_requires_whole_task_scope_and_clock_validity(value,valid,scope,expected):
    r=baseline();r['turns']=[{'body':r['runs'][0],'api_to_terminal_ms':value,'latency_valid':valid,'latency_scope':scope}]
    assert summarize_attempts(r)['latency_ms']==[expected]

@pytest.mark.parametrize('record',[None,[],{}, {'attempt_observation_version':999}, {'attempt_observation_version':1}])
def test_empty_or_unsupported_evidence_is_unavailable(record):
    out=summarize_attempts(record)
    assert not out['evidence_valid'] and not out['coverage_complete']
    assert out['model']['input_tokens'] is None and out['cost']['total'] is None

def test_impossible_attempt_count_is_rejected_before_range_allocation():
    r=baseline();r['runs'][0]['attempt_count']=10**12
    assert not summarize_attempts(r)['evidence_valid']

def test_old_read_score_stays_byte_equivalent_until_new_metrics_are_opted_in():
    import importlib.util
    from evals.read_task_score import score_read_task
    # Independent frozen scorer text is kept as a fixture during integration.
    spec=importlib.util.spec_from_file_location('r84_old_score',FIXTURE/'read_task_score_before.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    case={'id':'synthetic','expected_tools':[],'requires_nutrition':False}
    record={'baseline':{'synthetic':1},'final':{'synthetic':1},'turns':[],'tool_attempts':[],'tool_audits':[],'provider':[]}
    assert score_read_task(case,record,business_date='2026-10-09')==module.score_read_task(case,record,business_date='2026-10-09')
    record.update(baseline());record['turns']=[]
    out=score_read_task(case,record,business_date='2026-10-09')
    assert out['execution_metrics']['model']['observed_requests']==4
    assert out['semantic_pass'] is out['answer_accuracy'] is out['hallucination_rate'] is None

def test_zero_registered_tools_have_no_rate_and_missing_history_is_not_zero_cost():
    r=baseline();r['runs'][0]['attempt_count']=1
    h=r['attempt_observations'][0]['history'];h['attempts']=h['attempts'][:1]
    a=h['attempts'][0];a['tool_calls']=[];u=a['model_usage'];u['calls']=[]
    for key,value in list(u.items()):
        if type(value) is int:u[key]=0
    u.update(worker_attempt=1,prior_attempts_unobserved=False,observation_complete=True,input_tokens=0,output_tokens=0)
    h.update(usage_complete=True,input_tokens=0,output_tokens=0,known_input_tokens=0,known_output_tokens=0)
    out=summarize_attempts(r)
    assert out['evidence_valid'] and out['registered_read_intents']['observed']==0
    assert out['registered_read_intents']['success_rate'] is out['registered_read_intents']['error_rate'] is None
    assert out['model']['input_tokens']==0 and out['model']['full_request_count']==0
    r['attempt_observations']=[];out=summarize_attempts(r)
    assert not out['evidence_valid'] and out['model']['input_tokens'] is None and out['cost']['total'] is None

def test_unknown_transport_boundaries_keep_known_response_usage_but_not_full_totals():
    r=baseline()
    for a in r['attempt_observations'][0]['history']['attempts']:
        for c in a['model_usage']['calls']:c['transport_boundary_observed']=False
    out=summarize_attempts(r)
    assert out['evidence_valid'] and out['model']['known_observations']['known_input_tokens']==48
    assert out['model']['input_tokens'] is None and out['model']['full_request_count'] is None

def test_successful_http_with_missing_usage_has_unknown_cost():
    r=baseline();h=r['attempt_observations'][0]['history'];u=h['attempts'][1]['model_usage'];call=u['calls'][0]
    for key in ('input_tokens','output_tokens','cached_input_tokens'):call[key]=call['responses'][0][key]=None
    u.update(known_input_tokens=12,known_output_tokens=5,usage_known_requests=1)
    h.update(known_input_tokens=36,known_output_tokens=15,usage_complete=False,input_tokens=None,output_tokens=None)
    out=summarize_attempts(r,prices=PRICES)
    assert out['evidence_valid'] and out['model']['known_observations']['http_successes']==4
    assert out['model']['full_request_count']==4 and out['model']['input_tokens'] is None
    assert out['cost']['total_min'] is out['cost']['total_max'] is None

def test_batch_sums_all_observed_attempts_but_not_unknown_totals():
    records=list(RECORDS.values());out=summarize_batch(records)
    assert out['evidence_valid'] and out['instances']==24
    assert out['known_model_observations']['http_requests']==78
    assert out['known_model_observations']['known_input_tokens']==828
    assert out['known_model_observations']['known_output_tokens']==345
    assert out['registered_read_intents']['successes']==63 and out['registered_read_intents']['pending']==15
    assert out['registered_read_intents']['success_rate'] is None and out['tool_dispatch']['error_rate'] is None
    assert out['tokens']['mean_total'] is out['tokens']['p95_total'] is out['tokens']['input_total'] is None
    assert out['tokens']['complete_instances']==15
    assert out['estimated_cost']['total'] is out['turn_latency_ms']['p99'] is out['task_success_rate'] is None
    assert summarize_batch(records+[copy.deepcopy(records[0])])==out

def test_batch_refuses_conflicting_instance_or_shared_run():
    a=baseline();b=copy.deepcopy(a);b['extra']='conflict'
    out=summarize_batch([a,b]);assert not out['evidence_valid'] and out['tokens']['input_total'] is None
    b=copy.deepcopy(a);b['case_id']='different-case-same-run'
    out=summarize_batch([a,b]);assert not out['evidence_valid'] and out['aggregated_instances']==1
    assert out['known_model_observations']['http_requests']==4

@pytest.mark.parametrize('change',['result_status','missing_coroutine'])
def test_dispatch_cannot_hide_conflicting_journal_and_function_evidence(change):
    r=json.loads((FIXTURE/'runner-record.json').read_text(encoding='utf8'))
    original=summarize_attempts(r);assert original['tool_dispatch']['complete']
    o=r['attempt_observations'][0];a=o['history']['attempts'][0]
    if change=='result_status':a['tool_calls'][0]['result_json']='{"ok":false}'
    else:a['tool_calls'].pop()
    o['sql']['attempts'][0]['tool_calls']=copy.deepcopy(a['tool_calls'])
    out=summarize_attempts(r)
    assert not out['tool_dispatch']['complete'] and out['tool_dispatch']['success_rate'] is None

def test_valid_normal_batch_has_measured_tool_rate_but_no_business_or_human_score():
    r=json.loads((FIXTURE/'runner-record.json').read_text(encoding='utf8'))
    out=summarize_batch([r],prices=PRICES)
    assert out['evidence_valid'] and out['tool_dispatch']['attempts']==2 and out['tool_dispatch']['error_rate']==0
    assert out['tool_dispatch']['successes']==2 and out['tool_dispatch']['success_rate']==1
    assert out['tokens']['input_total']==24 and out['tokens']['output_total']==10
    assert out['tokens']['mean_total']==out['tokens']['p95_total']==34
    assert out['task_success_rate'] is out['human_takeover_rate'] is out['task_completion_time_ms'] is None

@pytest.mark.parametrize('records',[None,{},[],[None],[1],[{}]])
def test_malformed_or_empty_batch_is_unavailable(records):
    out=summarize_batch(records)
    assert not out['evidence_valid']
    assert out['tool_dispatch']['success_rate'] is out['tokens']['mean_total'] is out['estimated_cost']['total'] is None
