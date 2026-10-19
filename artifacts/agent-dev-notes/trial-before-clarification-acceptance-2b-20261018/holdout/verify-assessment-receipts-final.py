# R75 adaptation of the R74 post-run provenance verifier; frozen before live use.
"""Post-run provenance checks; does not certify semantic correctness."""
import hashlib,json
from pathlib import Path
R=Path(__file__).resolve().parent
budget=json.loads((R/'live-budget.json').read_text())
bench=json.loads((R/'benchmark.json').read_text())
assert len(budget['instances'])==len(bench['cases'])*bench['repeats'] and set(budget['instances'].values())=={'finished'}
rows=[]
for file in sorted((R/'runs').glob('*.json')):
    r=json.loads(file.read_text(encoding='utf8'))
    for ti,t in enumerate(r['turns']):
        tr=t['body']['execution_trace'];x=tr.get('assessment_delivery')
        if not x:continue
        assert hashlib.sha256(x['draft'].encode()).hexdigest()==x['draft_sha256']
        for key,source in [('findings',x['draft']),('remaining_findings',x['repair_draft'] or x['draft'])]:
            for finding in x[key]:assert source[finding['start']:finding['end']]==finding['quote']
        for cid in x['source_call_ids']:
            acts=[a for a in tr['actions'] if a['call_id']==cid and a['status']=='completed'];assert len(acts)==1
            obs=[o for o in tr['observations'] if o['call_id']==cid and o['status']=='success'];assert len(obs)==1
        ps=[p for p in r['provider'] if p['turn']==ti]
        drafts=[p for p in ps if any(c['message'].get('content')==x['draft'] for c in p['response'].get('choices',[]))];assert len(drafts)==1
        assert x['repair_attempts']==1
        repairs=[p for p in ps if any(c['message'].get('content')==x['repair_draft'] for c in p['response'].get('choices',[]))];assert len(repairs)==1
        repair=repairs[0];assert ps.index(repair)>ps.index(drafts[0])
        assert not repair['request'].get('tools') and not repair['request'].get('functions')
        assert repair['status']==200 and repair['complete'] and repair['lifecycle_complete']
        assert not any(c['message'].get('tool_calls') for c in repair['response']['choices'])
        assert '上一草稿包含已定位的无依据断言' in repair['request']['messages'][0]['content']
        assert tr['budget_usage']['model_calls']>=1
        assert x['checks']=='assertion_scope_v7'
        payload=json.loads(repair['request']['messages'][-1]['content'])
        value=x['repair_input'];assert payload['revision']==value
        assert hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==x['repair_input_sha256']
        assert 'draft' not in payload and 'findings' not in payload
        marker='[此处原句待重新形成：请用实际证据回答，不能复制原结论。]'
        parts=[];cursor=0
        for span in value['excluded_spans']:
            assert cursor<=span['start']<span['end']<=len(x['draft'])
            parts.extend([x['draft'][cursor:span['start']],marker]);cursor=span['end']
        parts.append(x['draft'][cursor:]);assert ''.join(parts)==value['working_draft']
        for f in x['findings']:assert any(s['start']<=f['start']<f['end']<=s['end'] and f['code'] in s['codes'] for s in value['excluded_spans'])
        assert marker not in t['body']['reply']
        assert payload['observations']
        if x['status']=='corrected':
            assert not x['remaining_findings']
            if t['body']['reply']!=x['repair_draft']:
                renders=json.loads((R/'corrected-render-verification.json').read_text())['rows']
                matches=[v for v in renders if v['id']==r['instance'] and v['turn']==ti]
                assert len(matches)==1 and matches[0]['exact_renderer_match']
                assert matches[0]['raw_sha256']==hashlib.sha256(file.read_bytes()).hexdigest()
                assert matches[0]['final_reply_sha256']==hashlib.sha256(t['body']['reply'].encode()).hexdigest()
        else:assert x['status']=='blocked' and not r['score']['terminal_delivery_eligible']
        rows.append({'id':r['instance'],'turn':ti,'status':x['status'],'codes':[f['code'] for f in x['findings']],'draft_attempt':drafts[0]['attempt_id'],'repair_attempt':repair['attempt_id'],'repair_tokens':repair['response']['usage']['total_tokens'],'receipt_and_wire_verified':True,'raw_sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
out={'rows':rows,'count':len(rows),'model_calls':0,'limitations':['Provenance only; corrected status is not semantic success.','Budget limit enforced by frozen implementation and offline tests; this checks receipt and actual one tool-free repair, not a new full concurrency audit.']}
with (R/'assessment-receipt-verification.json').open('x',encoding='utf8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps({'verified_receipts':len(rows),'repair_tokens':sum(r['repair_tokens'] for r in rows)}))
