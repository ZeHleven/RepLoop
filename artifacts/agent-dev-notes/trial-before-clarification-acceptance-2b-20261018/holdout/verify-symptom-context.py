"""Post-run prompt provenance; not a semantic or medical correctness grade."""
import hashlib,json,sys
from pathlib import Path
R=Path(__file__).resolve().parent
source=json.loads((R/'candidate-source.json').read_text());sys.path.insert(0,str(Path(source['source'])/'backend'))
from app.services.agent_movement_reference import movement_reference
gold=json.loads((R/'symptom-context-gold.json').read_text())
rows=[];contexts=[]
for file in sorted((R/'runs').glob('*.json')):
    r=json.loads(file.read_text(encoding='utf8'))
    for ti,turn in enumerate(r['turns']):
        found=[]
        for pr in [q for q in r['provider'] if q['turn']==ti]:
            for message in pr['request']['messages']:
                if message['role']!='system':continue
                for line in message.get('content','').splitlines():
                    if not line.startswith('{') or 'current_symptom_report' not in line:continue
                    data=json.loads(line);report=data['current_symptom_report']
                    assert report['quote']==turn['message'] and report['source']=='current_user_message'
                    assert report['sha256']==hashlib.sha256(turn['message'].encode()).hexdigest()
                    assert data['diagnosis']=='unknown' and data['movement_reference']==movement_reference()
                    assert all(x['quote'] in turn['message'] and x['source']=='current_user_message' for x in data['current_symptom_evidence'])
                    for cid in data['activation_plan_source_call_ids']:
                        audits=[x for x in r['tool_audits'] if x['call_id']==cid and x['run_id']==turn['body']['id'] and x['tool_name']=='plan.get_active' and x['status']=='completed']
                        assert len(audits)==1 and audits[0]['result_data'].get('found') is True
                    item={'id':r['instance'],'turn':ti,'provider_index':pr['index'],'current_report_sha256':report['sha256'],'plan_call_ids':data['activation_plan_source_call_ids']}
                    contexts.append(item);found.append(pr['index'])
        expected=gold['expected_context'][r['case_id']][ti]
        rows.append({'id':r['instance'],'turn':ti,'expected':expected,'provider_indices':found,'matched_expected_presence':bool(found)==expected,'risk_level':turn['body'].get('risk_level'),'request_kind':turn['body'].get('request_kind'),'raw_sha256':hashlib.sha256(file.read_bytes()).hexdigest()})
out={'verified_provider_contexts':len(contexts),'rows':rows,'contexts':contexts,'presence_mismatches':[x for x in rows if not x['matched_expected_presence']],'all_present_contexts_provenance_verified':True,'model_calls':0,'scope':'Full current user quote, hash, exact curated references and successful plan source IDs; expected presence is a separate diagnostic and does not certify final advice'}
with (R/'symptom-context-verification.json').open('x',encoding='utf8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in out.items() if k not in ['rows','contexts']},ensure_ascii=False))
