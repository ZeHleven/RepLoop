import asyncio,copy,json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from continuation_budget import ContinuationBudget,request_reservation,EvaluationStopped
from capture import ProviderCapture
from app.services.agent_model_usage import ObservedAsyncClient,observe_model_usage

ROOT=Path(__file__).resolve().parent
def payload(**changes):return json.dumps({'model':'deepseek-v4-flash','messages':[{'role':'user','content':'synthetic'}],'max_tokens':100,**changes})
def config(tmp_path):
    value=json.loads((ROOT/'continuation.json').read_text());value['source_hashes']={};value.pop('approved_limits',None)
    path=tmp_path/'continuation.json';path.write_text(json.dumps(value));return path
def budget(tmp_path,path=None):
    return ContinuationBudget(tmp_path/'budget.json','fixture',seconds=30,runs=40,requests=250,max_estimated_cny=3,
                              wall=lambda:100.,mono=lambda:50.,epoch=lambda:'synthetic',continuation_path=path or config(tmp_path))
def row(index=1,raw=None,status=200,prompt=12,completion=3):
    return {'index':index,'request_raw':raw or payload(),'complete':True,'lifecycle_complete':True,'status':status,
            'response':{'usage':{'prompt_tokens':prompt,'completion_tokens':completion}} if status==200 else {'error':'busy'}}


def test_fresh_batch_allows_all_thirty_slots_once_and_starts_at_zero(tmp_path):
    b=budget(tmp_path)
    assert b.state['requests']==b.state['runs']==b.state['carried_reserve_units']==0
    assert b.state['prior_requests']==b.state['prior_runs']==0
    bench=json.loads((ROOT/'benchmark.json').read_text(encoding='utf8'))
    instances=[c['id']+'-r'+str(rep) for c in bench['cases'] for rep in (1,2)]
    assert len(instances)==30
    for instance in instances:b.begin(instance)
    with pytest.raises(EvaluationStopped,match='already started'):b.begin(instances[0])


@pytest.mark.parametrize('kind,limit',[('runs',40),('requests',250)])
def test_fresh_batch_counter_limits_stop_before_extra_dispatch(tmp_path,kind,limit):
    b=budget(tmp_path)
    for index in range(1,limit+1):
        if kind=='runs':assert b.reserve(kind)==index
        else:
            assert b.reserve_request(payload())==index
            b.record_provider_cost(row(index))
    with pytest.raises(EvaluationStopped):
        if kind=='runs':b.reserve(kind)
        else:b.reserve_request(payload())
    assert b.state[kind]==limit


def test_success_releases_only_its_own_reservation(tmp_path):
    b=budget(tmp_path);i=b.reserve_request(payload());held=b.state['committed_estimate_units']
    b.record_provider_cost(row(i));assert b.state['committed_estimate_units']==0+12*200+3*800<held
    b.record_provider_cost(row(i));assert b.state['requests']==1
    b2=budget(tmp_path,tmp_path/'continuation.json');assert b2.state['provider_cost_units']==b.state['provider_cost_units']


def test_missing_usage_stays_reserved_and_sticky_across_restart(tmp_path):
    b=budget(tmp_path);i=b.reserve_request(payload());held=b.state['committed_estimate_units']
    b.record_provider_cost(row(i,status=503));assert b.state['stop']=='provider_cost_unknown'
    assert b.state['committed_estimate_units']==held and b.state['prior_actual_cost_cny'] is None
    restored=budget(tmp_path,tmp_path/'continuation.json')
    with pytest.raises(EvaluationStopped):restored.reserve_request(payload())
    assert restored.state['requests']==1


def test_pending_request_on_new_process_state_never_replays(tmp_path):
    b=budget(tmp_path);b.reserve_request(payload());before=b.state['committed_estimate_units']
    restored=budget(tmp_path,tmp_path/'continuation.json')
    assert restored.state['stop']=='unresolved_provider_reservation' and restored.state['committed_estimate_units']==before
    with pytest.raises(EvaluationStopped):restored.reserve_request(payload())


@pytest.mark.parametrize('bad',[{},[],{'max_tokens':None},{'max_tokens':True},{'max_tokens':-1},{'model':'another-model'},
                              {'messages':[{'role':'user','content':[{'type':'image_url'}]}]},{'max_completion_tokens':200}])
def test_invalid_or_unbounded_requests_stop_before_reserving(tmp_path,bad):
    b=budget(tmp_path)
    raw=json.dumps(bad) if bad in ({},[]) else payload(**bad)
    with pytest.raises(EvaluationStopped):b.reserve_request(raw)
    assert b.state['requests']==0 and b.state['stop']=='request_estimate_invalid'


def test_cost_reservation_refuses_request_before_dispatch(tmp_path):
    b=budget(tmp_path)
    with pytest.raises(EvaluationStopped):b.reserve_request(payload(max_tokens=384000))
    assert b.state['requests']==0 and b.state['stop']=='estimated_cost_reservation_limit'


def test_actual_usage_exceeding_output_cap_stops_even_if_total_below_three_yuan(tmp_path):
    b=budget(tmp_path);i=b.reserve_request(payload());b.record_provider_cost(row(i,completion=101))
    assert b.state['stop']=='provider_usage_exceeded_reservation'


@pytest.mark.parametrize('change',['request','conflict','unreserved'])
def test_receipts_cannot_change_identity_or_cost(tmp_path,change):
    b=budget(tmp_path);i=b.reserve_request(payload());r=row(i)
    if change=='request':r['request_raw']=payload(max_tokens=99)
    elif change=='unreserved':r['index']=2
    else:b.record_provider_cost(r);r['response']['usage']['prompt_tokens']=13
    b.record_provider_cost(r);assert b.state['stop']=='provider_cost_unknown'


def test_prior_file_tamper_rejected_before_budget_creation(tmp_path):
    path=config(tmp_path);v=json.loads(path.read_text());v['source_hashes']={'source.json':'0'*64};path.write_text(json.dumps(v));(tmp_path/'source.json').write_text('{}')
    with pytest.raises(EvaluationStopped,match='prior_evidence_changed'):budget(tmp_path,path)
    assert not (tmp_path/'budget.json').exists()


def test_concurrent_reservations_are_unique_and_all_remain_budgeted(tmp_path):
    b=budget(tmp_path)
    with ThreadPoolExecutor(4) as pool:values=list(pool.map(lambda _:b.reserve_request(payload()),range(8)))
    state=json.loads((tmp_path/'budget.json').read_text())
    assert sorted(values)==list(range(1,9)) and state['requests']==8
    assert state['committed_estimate_units']==0+8*request_reservation(payload())['units']


@pytest.mark.asyncio
async def test_actual_capture_and_application_distinguish_unknown_response_from_local_retry(tmp_path):
    b=budget(tmp_path);captured=[];sent=[];guards=[]
    capture=ProviderCapture(lambda r:captured.append(copy.deepcopy(r)),guard_sink=guards.append);capture.budget=b
    async def provider(request):sent.append(True);return httpx.Response(503,json={'error':'busy'})
    capture.install()
    try:
        async with observe_model_usage(1) as ledger:
            async with ObservedAsyncClient(source='agent',transport=httpx.MockTransport(provider)) as client:
                await client.post('https://api.deepseek.com/chat/completions',content=payload())
                with pytest.raises(EvaluationStopped):await client.post('https://api.deepseek.com/chat/completions',content=payload())
            usage=ledger.snapshot(complete=True)
    finally:capture.uninstall()
    assert len(sent)==1 and usage.client_attempts==2 and usage.local_errors==1
    assert usage.transport_attempts==usage.http_responses==usage.http_errors==1 and usage.transport_errors==0
    assert b.state['requests']==1 and b.state['stop']=='provider_cost_unknown'
    assert captured[-1]['lifecycle_complete'] and captured[-1]['status']==503
    assert len(guards)==usage.local_errors and guards[0]['stage']=='before_provider_send'
