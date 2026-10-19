"""One authorized pair, reusing frozen benchmark, gold, runner and budgets."""
from pathlib import Path
import hashlib,json,shutil
R=Path(__file__).resolve().parent;B=R.parent
OLD=B/'guard-fallback-acceptance-20261018';FIX=B/'trial-before-clarification-local-fix-2b'
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x',encoding='utf8') as f:json.dump(v,f,ensure_ascii=False,indent=2)
pool=read(B/'authorized-retests-60/ledger.json')
assert not pool['stop'] and pool['known_units']==pool['committed_units']==1490320804
assert all(x['status']=='settled' for x in pool['batches'].values())
fix=read(FIX/'result.json')
assert fix['related']['passed']==37 and fix['core_smoke']['passed']==4
assert fix['holdout_boundary_replay']['HO04-r1']==fix['holdout_boundary_replay']['HO06-r1']=='pass'
frozen=read(FIX/'frozen-candidate.json');verified={f['file']:f['sha256'] for f in frozen['files']}
src=read(OLD/'dev/candidate-source.json');assert len(src['files'])==len(verified)==539
changed=[]
for f in src['files']:
    actual=sha(Path(src['source'])/f['file']);assert actual==verified[f['file']],f['file']
    if actual!=f['sha256']:changed.append(f['file'])
    f['sha256']=actual
assert changed==['backend/app/services/agent_action_consistency.py']
save(R/'pool-before.json',pool)
save(R/'authorization.json',dict(source='User: 授权真实验收, following local-fix-2b completion. One frozen original30+holdout10 DeepSeek pair <=5 CNY from existing 60 CNY pool; score and stop. No second pair, failure reruns, patches, or R157.',pair_count=1,pair_cost_limit_cny=5,dev_instances=30,holdout_instances=10,cumulative_before_cny=14.90320804,after_results='停等用户'))
shutil.copyfile(OLD/'holdout-gold.json',R/'holdout-gold.json')
pricing=read(OLD/'pricing-check.json');pricing['checked_date']='2026-10-18';pricing['checked_again_before_this_pair']=True
save(R/'pricing-check.json',pricing)
for name in ['host.cjs','pool.py','review_read.py','review_panel.py','compile_results.py']:
    (R/name).write_text((OLD/name).read_text(encoding='utf8').replace('guardaccept1','trialaccept2b'),encoding='utf8')
p=R/'compile_results.py';s=p.read_text(encoding='utf8').replace('trial-clarification-acceptance-20261018','guard-fallback-acceptance-20261018')
s=s.replace("baseline_task='trial-clarification-acceptance',experiment='guard-fallback-acceptance'", "baseline_task='guard-fallback-acceptance',experiment='trial-before-clarification-acceptance-2b'")
s=s.replace('reused_same_candidate_local_passes=26','reused_same_candidate_local_passes=43')
p.write_text(s,encoding='utf8')
prior_files={str(p.relative_to(B)):sha(p) for p in [OLD/'result.json',OLD/'review-notes.json',OLD/'holdout-gold.json',OLD/'dev/business-review.json',FIX/'result.json',FIX/'frozen-candidate.json']}
save(R/'preparation.json',dict(application_changes=0,source_files_verified=539,changes_since_previous_acceptance=changed,dev_cases_unchanged=True,holdout_cases_unchanged=True,holdout_gold_unchanged=True,cumulative_before_cny=14.90320804,prior_files=prior_files,reused_local_acceptance='../trial-before-clarification-local-fix-2b/result.json',new_local_regression=0))
for batch in ['dev','holdout']:
    D=R/batch;D.mkdir();old=OLD/batch
    for p in old.iterdir():
        if p.suffix=='.py' or p.name in ['required-facts.json','symptom-context-gold.json','provider-pricing-reference.json','continuation.json']:shutil.copyfile(p,D/p.name)
    b=read(old/'benchmark.json');b['id']='trialaccept2b-'+batch;save(D/'benchmark.json',b)
    (D/'run.py').write_text((D/'run.py').read_text(encoding='utf8').replace('guardaccept1','trialaccept2b'),encoding='utf8')
    for k,v in read(old/'benchmark.json').items():
        if k!='id':assert b[k]==v
    save(D/'candidate-source.json',src)
    files={p.name:sha(p) for p in D.iterdir() if p.suffix=='.py' or p.name in ('benchmark.json','candidate-source.json','continuation.json','required-facts.json','symptom-context-gold.json','provider-pricing-reference.json')}
    save(D/'freeze.json',dict(purpose='One frozen trial-before-clarification-acceptance-2b pair; no application changes or reruns',new_instances=len(b['cases'])*b['repeats'],limits=b['limits'],files=files,host_sha256=sha(R/'host.cjs'),pool_sha256=sha(R/'pool.py'),holdout_gold_sha256=sha(R/'holdout-gold.json'),authorization_sha256=sha(B/'authorized-retests-60/authorization.json'),round_authorization_sha256=sha(R/'authorization.json'),semantic_review='Original frozen gold and prior calibrations; review every final turn, care and actual equipment/grip; necessary clarification alone does not satisfy omitted goals; no tuning'))
print('539 source fingerprints verified; frozen original30+holdout10; allocations 4+1 CNY; no paid requests yet.')
