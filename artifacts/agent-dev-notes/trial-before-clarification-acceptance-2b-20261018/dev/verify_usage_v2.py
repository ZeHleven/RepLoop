"""Independent response/guard capture versus persisted application observations.

Ambiguous raw transport failures need separate investigation, never a green
comparison against an empty response list. This audit is not a business score.
"""
import copy,json,hashlib
from pathlib import Path

def token(v):return v if type(v) is int and v>=0 else None
def expected_response(row):
    assert row.get('complete') and row.get('lifecycle_complete') and isinstance(row.get('status'),int) and not row.get('error_type'),'raw response incomplete or ambiguous'
    usage=(row.get('response') or {}).get('usage') or {};details=usage.get('prompt_tokens_details') or {}
    incoming=token(usage.get('prompt_tokens'));outgoing=token(usage.get('completion_tokens'))
    cached=token(usage.get('prompt_cache_hit_tokens',details.get('cached_tokens')))
    if cached is not None and (incoming is None or cached>incoming):cached=None
    return dict(http_status=row['status'],input_tokens=incoming,output_tokens=outgoing,cached_input_tokens=cached)

def check(body,saved,providers,guards):
    u=body['model_usage'];assert u==saved['model_usage'] and u['version']=='transport_boundary_v2'
    calls=u['calls'];assert [c['sequence'] for c in calls]==list(range(1,len(calls)+1))
    expected=[expected_response(p) for p in providers]
    actual=[r for c in calls for r in c['responses']];assert actual==expected,'response/usage mismatch'
    assert all(g['stage']=='before_provider_send' for g in guards)
    assert u['client_attempts']==len(calls)==len(providers)+len(guards),'client denominator mismatch'
    assert u['http_responses']==u['http_requests']==len(providers)
    assert u['transport_attempts']==sum(c['transport_attempts'] for c in calls)==len(providers)
    assert u['local_errors']==sum(c['outcome']=='local_error' for c in calls)==len(guards)
    assert u['transport_errors']==sum(c['transport_errors'] for c in calls)==0
    assert u['unclassified_client_errors']==u['pending_or_cancelled']==0
    for call in calls:
        assert call['transport_boundary_observed']
        if call['responses']:
            last=call['responses'][-1]
            assert call['outcome']==('http_success' if 200<=last['http_status']<300 else 'http_error')
            for k,v in last.items():assert call[k]==v
        else:assert call['outcome']=='local_error' and call['transport_attempts']==0
    assert u['http_successes']==sum(200<=r['http_status']<300 for r in expected)
    assert u['http_errors']==sum(not 200<=r['http_status']<300 for r in expected)
    known=sum(r['input_tokens'] is not None and r['output_tokens'] is not None for r in expected)
    incoming=sum(r['input_tokens'] or 0 for r in expected);outgoing=sum(r['output_tokens'] or 0 for r in expected)
    assert u['usage_known_requests']==known and u['known_input_tokens']==incoming and u['known_output_tokens']==outgoing
    assert u['worker_attempt']==body['attempt_count']==saved['attempt_count']
    assert u['prior_attempts_unobserved']==(body['attempt_count']>1)
    full=u['observation_complete'] and known==len(expected) and not u['prior_attempts_unobserved']
    assert body['input_tokens']==saved['input_tokens']==u['input_tokens']==(incoming if full else None)
    assert body['output_tokens']==saved['output_tokens']==u['output_tokens']==(outgoing if full else None)
    return dict(responses=len(expected),local_guards=len(guards),known_input_tokens=incoming,known_output_tokens=outgoing,full_usage_known=full)

def main(root):
    rows=[];unverified=[]
    for file in sorted((root/'runs').glob('*.json')):
        rec=json.loads(file.read_text());turns=rec['turns']
        if not turns:unverified.append({'instance':rec['instance'],'reason':'no terminal API turn; inspect scoped SQL separately'});continue
        for i,t in enumerate(turns):
            body=t['body'];saved=next(r for r in rec['runs'] if r['id']==body['id'])
            result=check(body,saved,[p for p in rec['provider'] if p['turn']==i],[g for g in rec['provider_guard_events'] if g['turn']==i])
            rows.append({'instance':rec['instance'],'turn':i,'raw_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),**result})
    result={'passed':bool(rows) and not unverified,'verified_turns':len(rows),'unverified':unverified,'rows':rows,'business_scored':False}
    with (root/'provider-usage-v2-verification.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))

if __name__=='__main__':main(Path(__file__).resolve().parent)
