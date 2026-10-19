"""Compare API/SQL provider usage against the independent raw HTTP capture.

No business success or protocol correctness can be inferred from this audit.
"""
import json,hashlib,sys,copy
from pathlib import Path


def token(value):return value if type(value) is int and value>=0 else None


def expected_call(provider):
    status=provider.get('status')
    kind=('cancelled' if provider.get('error_type')=='CancelledError' else 'transport_error') if status is None else 'http_success' if 200<=status<300 else 'http_error'
    body=provider.get('response');usage=body.get('usage') if isinstance(body,dict) else None
    usage=usage if isinstance(usage,dict) else {}
    incoming=token(usage.get('prompt_tokens'));outgoing=token(usage.get('completion_tokens'))
    detail=usage.get('prompt_tokens_details');cache=token(usage.get('prompt_cache_hit_tokens',detail.get('cached_tokens') if isinstance(detail,dict) else None))
    if cache is not None and (incoming is None or cache>incoming):cache=None
    return dict(outcome=kind,http_status=status,input_tokens=incoming,output_tokens=outgoing,cached_input_tokens=cache)


def check(body,saved,providers):
    usage=body['model_usage'];assert isinstance(usage,dict),'missing provider telemetry'
    assert saved['model_usage']==usage,'API/SQL mismatch'
    assert usage['scope']=='worker_attempt' and usage['version']=='provider_http_v1'
    calls=usage['calls'];assert len(calls)==len(providers)==usage['http_requests'],'HTTP denominator mismatch'
    expected=[expected_call(p) for p in providers]
    for i,(call,want) in enumerate(zip(calls,expected),1):
        assert call['sequence']==i
        for k,v in want.items():assert call[k]==v,(i,k,call[k],v)
    incoming=sum(x['input_tokens'] or 0 for x in expected);outgoing=sum(x['output_tokens'] or 0 for x in expected)
    known=sum(x['input_tokens'] is not None and x['output_tokens'] is not None for x in expected)
    assert usage['known_input_tokens']==incoming and usage['known_output_tokens']==outgoing
    assert usage['usage_known_requests']==known
    for name,outcomes in [('http_successes',{'http_success'}),('http_errors',{'http_error'}),('transport_errors',{'transport_error'}),('pending_or_cancelled',{'pending','cancelled'})]:
        assert usage[name]==sum(x['outcome'] in outcomes for x in expected),name
    assert usage['worker_attempt']==body['attempt_count']==saved['attempt_count']
    assert usage['prior_attempts_unobserved']==(body['attempt_count']>1)
    complete=usage['observation_complete'] and known==len(expected) and not usage['prior_attempts_unobserved'] and not usage['pending_or_cancelled']
    assert body['input_tokens']==saved['input_tokens']==usage['input_tokens']==(incoming if complete else None)
    assert body['output_tokens']==saved['output_tokens']==usage['output_tokens']==(outgoing if complete else None)
    return {'provider_requests':len(expected),'usage_known_requests':known,'input_tokens':body['input_tokens'],'output_tokens':body['output_tokens'],'known_input_tokens':incoming,'known_output_tokens':outgoing,'scope':'worker_attempt','full_run_tokens_known':complete}


def selftest():
    p={'status':200,'response':{'usage':{'prompt_tokens':12,'completion_tokens':5}}}
    def fixture():
        u=dict(version='provider_http_v1',scope='worker_attempt',worker_attempt=1,prior_attempts_unobserved=False,observation_complete=True,http_requests=1,http_successes=1,http_errors=0,transport_errors=0,pending_or_cancelled=0,usage_known_requests=1,known_input_tokens=12,known_output_tokens=5,input_tokens=12,output_tokens=5,calls=[dict(sequence=1,source='structured',**expected_call(p))])
        b=dict(attempt_count=1,model_usage=u,input_tokens=12,output_tokens=5);return b,copy.deepcopy(b)
    b,s=fixture();assert check(b,s,[p])['full_run_tokens_known']
    bad=0
    for change in ['missing_call','double_tokens','sql_mismatch','fake_zero','wrong_status','wrong_cache','wrong_attempt']:
        b,s=fixture()
        if change=='missing_call':b['model_usage']['calls']=[]
        elif change=='double_tokens':b['input_tokens']=24
        elif change=='sql_mismatch':s['output_tokens']=6
        elif change=='fake_zero':b['model_usage']['known_input_tokens']=0
        elif change=='wrong_status':b['model_usage']['http_errors']=1
        elif change=='wrong_cache':b['model_usage']['calls'][0]['cached_input_tokens']=12
        else:b['attempt_count']=2
        if change!='sql_mismatch':s=copy.deepcopy(b)
        try:check(b,s,[p])
        except AssertionError:bad+=1
    assert bad==7
    return {'passed':True,'positive':1,'negative':7,'provider_calls':0}


def main(root):
    selftest();rows=[]
    for file in sorted((root/'runs').glob('*.json')):
        r=json.loads(file.read_text(encoding='utf8'))
        for ti,t in enumerate(r['turns']):
            body=t['body'];saved=next(x for x in r['runs'] if x['id']==body['id'])
            providers=[p for p in r['provider'] if p['turn']==ti]
            row=check(body,saved,providers)
            rows.append({'instance':r['instance'],'turn':ti,'run_id':body['id'],'raw_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),**row})
    out={'passed':True,'runs':len(rows),'provider_requests':sum(x['provider_requests'] for x in rows),'known_input_tokens':sum(x['known_input_tokens'] for x in rows),'known_output_tokens':sum(x['known_output_tokens'] for x in rows),'full_tokens_known_runs':sum(x['full_run_tokens_known'] for x in rows),'rows':rows,'not_validated':['business success','protocol correctness','process crash durability','prices or cost estimates']}
    with (root/'provider-usage-verification.json').open('x',encoding='utf8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in out.items() if k!='rows'}))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='selftest':print(json.dumps(selftest()))
    else:main(Path(__file__).resolve().parent)
