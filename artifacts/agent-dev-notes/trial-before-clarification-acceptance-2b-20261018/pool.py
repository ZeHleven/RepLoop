"""Reserve a whole batch before external access; retain uncertain money on failure."""
from pathlib import Path
import hashlib,json,sys
R=Path(__file__).resolve().parent
sys.path.insert(0,str(R/'dev'))
from durable_budget import atomic_json,file_lock
read=lambda p:json.loads(p.read_text(encoding='utf8'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
UNIT=100_000_000

def reserve(root,batch,freeze,max_cny=5):
    root=Path(root);auth=root/'authorization.json';path=root/'ledger.json'
    with file_lock(str(path)+'.lock'):
        contract=read(auth)
        s=read(path) if path.exists() else dict(authorization_sha256=sha(auth),limit_units=contract['total_cny']*UNIT,batches={},stop=None)
        assert s['authorization_sha256']==sha(auth),'authorization_changed'
        assert not s['stop'],s['stop']
        assert batch not in s['batches'],'batch_already_reserved_no_replay'
        assert not any(x['status']=='reserved' for x in s['batches'].values()),'prior_batch_unsettled'
        assert type(max_cny) is int and 0<max_cny<=contract['limits']['max_estimated_cny'],'invalid_allocation'
        amount=max_cny*UNIT
        assert sum(x['charge_units'] for x in s['batches'].values())+amount<=s['limit_units'],'total_budget_reservation_limit'
        s['batches'][batch]=dict(status='reserved',freeze_sha256=freeze,charge_units=amount,known_units=None)
        atomic_json(path,s)
        return s

def settle(root,batch,directory,code):
    root=Path(root);D=Path(directory);path=root/'ledger.json'
    with file_lock(str(path)+'.lock'):
        s=read(path);entry=s['batches'][batch];assert entry['status']=='reserved'
        b=read(D/'live-budget.json') if (D/'live-budget.json').exists() else None
        bench=read(D/'benchmark.json');n=len(bench['cases'])*bench['repeats']
        reason=None
        if code!=0 or not b:reason='nonzero_or_missing_terminal_budget'
        elif b.get('stop'):reason=b['stop']
        elif len(b['instances'])!=n or b['requests']>400 or b['runs']>60:reason='incomplete_or_over_limit'
        elif any(x['status']!='known' for x in b['request_reservations'].values()):reason='unknown_usage'
        elif b['estimated_cost_units']!=b['committed_estimate_units'] or b['estimated_cost_units']>entry['charge_units']:reason='cost_mismatch_or_over_limit'
        elif b['last_mono']-b['started_mono']>2400:reason='time_over_limit'
        if b:entry['known_units']=b.get('estimated_cost_units')
        if reason:
            entry.update(status='abnormal_reserved',reason=reason)
            s['stop']=reason
        else:entry.update(status='settled',charge_units=b['estimated_cost_units'],budget_sha256=sha(D/'live-budget.json'))
        s['committed_units']=sum(x['charge_units'] for x in s['batches'].values())
        s['known_units']=sum(x.get('known_units') or 0 for x in s['batches'].values())
        if s['committed_units']>=s['limit_units']:s['stop']=s['stop'] or 'total_budget_reached'
        atomic_json(path,s);return s

if __name__=='__main__':
    mode,name=sys.argv[1:3];D=R/name;pool=R.parent/'authorized-retests-60';batch='trialaccept2b-'+name
    assert name in ['dev','holdout'] and mode in ['reserve','settle']
    authorization=read(R/'authorization.json')
    assert authorization['pair_count']==1 and authorization['pair_cost_limit_cny']==5
    if mode=='reserve' and name=='holdout':
        assert read(pool/'ledger.json')['batches']['trialaccept2b-dev']['status']=='settled'
    s=reserve(pool,batch,sha(D/'freeze.json'),4 if name=='dev' else 1) if mode=='reserve' else settle(pool,batch,D,int(sys.argv[3]))
    print(json.dumps(dict(batch=batch,entry=s['batches'][batch],cumulative_known_cny=sum(x.get('known_units') or 0 for x in s['batches'].values())/UNIT,stop=s['stop'])))
    if s['stop']:sys.exit(2)
