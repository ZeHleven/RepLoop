from pathlib import Path
import hashlib,json,collections
R=Path(__file__).resolve().parent
read=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,v):
    with p.open('x',encoding='utf8') as f:json.dump(v,f,ensure_ascii=False,indent=2)
notes=read(R/'review-notes.json');pool=read(R.parent/'authorized-retests-60/ledger.json');assert not pool['stop']
results={};reviews={}
for name,n in [('dev',30),('holdout',10)]:
    D=R/name;records={p.stem:read(p) for p in sorted((D/'runs').glob('*.json'))};assert len(records)==n
    rows=[r for k,v in notes.items() if k==name or k.startswith(name+'_') for r in v] if name=='dev' else notes['holdout']
    assert len(rows)==n and len({x['id'] for x in rows})==n and set(records)=={x['id'] for x in rows}
    budget=read(D/'live-budget.json');assert not budget['stop'] and len(budget['instances'])==n
    audit=read(D/'provider-usage-v2-verification.json');assert audit['passed']
    freeze=read(D/'freeze.json')
    for p,h in freeze['files'].items():assert sha(D/p)==h
    src=read(D/'candidate-source.json')
    for f in src['files']:assert sha(Path(src['source'])/f['file'])==f['sha256']
    provider=[];tools=[];turns=[];cost=0;incoming=0;outgoing=0
    for row in rows:
        rec=records[row['id']];assert rec.get('finished_at') and not rec.get('error')
        assert rec['baseline']==rec['final']
        alltext='\n'.join(t['body'].get('reply') or '' for t in rec['turns'])
        drafts='\n'.join(str(t['body'].get('execution_trace',{}).get('assessment_delivery')) for t in rec['turns'])
        for q in row['quotes']:assert q in alltext,(row['id'],q)
        for q in row.get('draft_quotes',[]):assert q in drafts,(row['id'],q)
        if row['pass']:assert not any(t['body'].get('error_code') for t in rec['turns'])
        row.update(raw_file=f'{name}/runs/{row["id"]}.json',raw_sha256=sha(D/'runs'/(row['id']+'.json')),all_final_turns_reviewed=True,source_facts_checked=True)
        provider+=rec['provider'];tools+=rec['tool_attempts'];turns+=rec['turns']
    assert sorted(x['index'] for x in provider)==list(range(1,budget['requests']+1))
    for p in provider:
        assert p['status']==200 and p['complete'] and p['lifecycle_complete']
        u=p['response']['usage'];hit=u.get('prompt_cache_hit_tokens',(u.get('prompt_tokens_details') or {}).get('cached_tokens',0))
        incoming+=u['prompt_tokens'];outgoing+=u['completion_tokens'];cost+=hit*4+(u['prompt_tokens']-hit)*200+u['completion_tokens']*800
    assert cost==budget['estimated_cost_units']==budget['committed_estimate_units']==pool['batches']['trialaccept2b-'+name]['charge_units']
    assert len(turns)==audit['verified_turns']
    summary=read(D/'results-first.json')['execution_summary'];assert summary['evidence_valid']
    assert summary['known_model_observations']['known_input_tokens']==incoming and summary['known_model_observations']['known_output_tokens']==outgoing
    passed=sum(x['pass'] for x in rows);care=sum(x['care_errors'] for x in rows);equipment=sum(x['equipment_grip_errors'] for x in rows)
    definition=sum(x.get('general_equipment_definition_errors',0) for x in rows)
    results[name]=dict(passed=passed,total=n,task_success_rate=passed/n,failure_distribution=dict(collections.Counter(x['category'] for x in rows if not x['pass'])),care_judgment_errors=care,actual_equipment_grip_errors=equipment,general_equipment_definition_errors=definition,provider_requests=len(provider),provider_http_successes=sum(x['status']==200 for x in provider),runs=budget['runs'],tools=len(tools),tool_successes=sum(x['status']=='success' and x['lifecycle_complete'] for x in tools),tool_errors=sum(x['status']!='success' or not x['lifecycle_complete'] for x in tools),input_tokens=incoming,output_tokens=outgoing,tokens=incoming+outgoing,peak_estimated_cny=cost/1e8,actual_invoice_cny=None,valid_timed_turns=sum(t['latency_valid'] for t in turns),turns=len(turns),full_task_p99_ms=None,concurrency_p99_ms=None,batch_monotonic_seconds=budget['last_mono']-budget['started_mono'],unchanged_business_snapshots=n)
    ledgers=[records[row['id']]['score']['tool_ledger'] for row in rows]
    assert all(l['complete'] and not l['issues'] for l in ledgers)
    dispatches=sum(l['attempts'] for l in ledgers);errors=sum(l['errors'] for l in ledgers)
    results[name].update(tool_dispatch_attempts=dispatches,tool_dispatch_successes=sum(l['successes'] for l in ledgers),tool_dispatch_errors=errors,tool_call_error_rate=errors/dispatches if dispatches else None,dispatcher_rejections=sum(l['dispatcher_rejections'] for l in ledgers),tool_metric_scope='Dispatch/action includes rejected model tool calls; function invocation metrics separate. Business success does not erase failures.')
    reviews[name]=rows
    # Persist only after every validation has passed.
old={r['id']:r for r in read(R.parent/'guard-fallback-acceptance-20261018/dev/business-review.json')['rows']};now={r['id']:r for r in reviews['dev']}
comparison=dict(previous_passed=sum(x['pass'] for x in old.values()),current_passed=results['dev']['passed'],recovered=[k for k in now if now[k]['pass'] and not old[k]['pass']],regressed=[k for k in now if not now[k]['pass'] and old[k]['pass']],note='Known development set, stochastic samples; not causal proof or blind review')
gate=results['dev']['passed']>=26 and results['holdout']['passed']>=8 and all(x['care_judgment_errors']==x['actual_equipment_grip_errors']==0 for x in results.values())
for p,h in read(R/'preparation.json')['prior_files'].items():assert sha(R.parent/p)==h
for name in results:
    freeze=read(R/name/'freeze.json')
    assert sha(R/'host.cjs')==freeze['host_sha256']
    assert sha(R/'pool.py')==freeze['pool_sha256']
    assert sha(R/'holdout-gold.json')==freeze['holdout_gold_sha256']
    assert sha(R.parent/'authorized-retests-60/authorization.json')==freeze['authorization_sha256']
    pg=read(R/name/'postgres-final-state.json');assert not pg['Running'] and pg['ExitCode']==0
final=dict(baseline_task='guard-fallback-acceptance',experiment='trial-before-clarification-acceptance-2b',status='Paired real acceptance completed',application_changes=0,source_files_verified=539,checks=dict(reused_same_candidate_local_passes=43,new_local_suite=0),batches=results,comparison=comparison,batch_pair_estimated_cny=sum(x['peak_estimated_cny'] for x in results.values()),cumulative_estimated_cny=pool['known_units']/1e8,total_authorized_cny=60,pair_gate_passed=gate,consecutive_qualified_pairs=1 if gate else 0,holdout_non_tuning=True,answer_accuracy=None,overall_hallucination_rate=None,human_takeover_rate=None,uncovered='Actual invoice, independent blind reviewer, real users, full atomic claim inventory, complete/concurrent P99',next_unique_task=notes['next_unique_task'])
final['prior_pair_consecutive']=read(R.parent/'guard-fallback-acceptance-20261018/result.json')['consecutive_qualified_pairs']
final['consecutive_qualified_pairs']=(final['prior_pair_consecutive']+1) if gate else 0
final['status']='Paused after the single authorized paired acceptance; no second pair or repairs without confirmation'
final['critical_error_instances']=sum(sum(bool(r['care_errors'] or r['equipment_grip_errors']) for r in rows) for rows in reviews.values())
final['urgent_care_error_instances']=sum(r['care_errors'] for r in reviews['holdout'] if r['id'].startswith(('HO09','HO10')))
final['holdout_comparison']={'previous_passed':5,'current_passed':results['holdout']['passed'],'acceptance_only':True}
final['provider_alias_backend_change']=read(R/'pricing-check.json')
final['previous_cumulative_estimated_cny']=read(R/'preparation.json')['cumulative_before_cny']
final['remaining_authorized_estimated_cny']=60-final['cumulative_estimated_cny']
final['model_and_tool_metrics_scope']='HTTP success, actual function invocations and dispatch/action attempts reported separately; tool_call_error_rate includes dispatcher rejections. None implies business correctness or covers omitted required tools.'
for name,rows in reviews.items():
    save(R/name/'business-review.json',dict(reviewer=notes['reviewer'],policy=notes['policy'],rows=rows,summary=results[name]))
save(R/'result.json',final);print(json.dumps(final,ensure_ascii=False,indent=2))
