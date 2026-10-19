"""Independent source/time bounds check, never a semantic answer success judge."""
import hashlib,json,re
from pathlib import Path
R=Path(__file__).resolve().parent
rows=[]
for f in sorted((R/'runs').glob('*.json')):
    run=json.loads(f.read_text(encoding='utf8'))
    for i,provider in enumerate(run['provider']):
        for message in provider['request'].get('messages',[]):
            if message['role']!='system' or not isinstance(message.get('content'),str):continue
            for line in message['content'].splitlines():
                if not line.startswith('{"training_adjustment_envelope":'):continue
                item=json.loads(line);e=item['training_adjustment_envelope'];cid=item['plan_source_call_id']
                audits=[a for a in run['tool_audits'] if a['call_id']==cid and a['tool_name']=='plan.get_active' and a['status']=='completed'];assert len(audits)==1
                turn=next(t for t in run['turns'] if t['body']['id']==audits[0]['run_id'])
                action=next(a for a in turn['body']['execution_trace']['actions'] if a['call_id']==cid)
                tools=[t for t in run['tool_attempts'] if t['tool']=='plan.get_active' and t.get('turn',0)==run['turns'].index(turn) and t['status']=='success'];assert len(tools)==1
                data=tools[0]['result'];canonical=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str)
                assert hashlib.sha256(canonical.encode()).hexdigest()==item['plan_result_sha256']
                plan=data['plan'];raw=plan['exercises'];total=sum(x['sets'] for x in raw)
                assert e['baseline_weekly_sets']==total and e['baseline_days']==plan['days_per_week']
                assert 0<=e['temporary_weekly_sets']<total
                assert e['temporary_days']==len(e['sessions'])<=min(2,plan['days_per_week'])
                caps={int(n) for n in re.findall(r'(\d+)\s*分钟',turn['message'])}
                if item['limit_source']=='validated_active_task_requirements':
                    assert not caps
                    state=turn['body']['execution_trace']['task_state'];assert state['transition'] in {'continue','resume'}
                    task=next(t for t in state['tasks'] if t['id']==state['active_task_id'])
                    for q in item['limit_quotes']:assert any(r['quote']==q['quote'] and r['source_run_id']==q['source_run_id'] for r in task['requirements'])
                    caps={int(n) for q in item['limit_quotes'] for n in re.findall(r'(\d+)\s*分钟',q['quote'])}
                if e['session_cap_minutes'] is None:
                    assert not caps and item['limit_source']=='unspecified_by_user' and not item['limit_quotes']
                else:
                    assert caps=={e['session_cap_minutes']}
                counted=0
                for s in e['sessions']:
                    seconds=240
                    for x in s['exercises']:
                        src=raw[x['source_index']];assert src['day_of_week']==s['original_day']
                        assert 0<x['sets']<=src['sets'] and x['rest_seconds']==src['rest_seconds']
                        assert 0<x['reps']<=int(re.split('[-–~～]',str(src['reps']))[0])
                        counted+=x['sets'];seconds+=x['sets']*(4*x['reps']+x['rest_seconds'])
                    assert seconds==s['estimated_seconds']
                    if e['session_cap_minutes'] is not None:assert seconds<=e['session_cap_minutes']*60
                assert counted==e['temporary_weekly_sets']
                rows.append({'id':run['instance'],'provider_index':i,'call_id':cid,'weekly_sets':counted,'minutes':e['session_cap_minutes'],'raw_sha256':hashlib.sha256(f.read_bytes()).hexdigest()})
out={'verified_provider_envelopes':len(rows),'distinct_task_instances':len({r['id'] for r in rows}),'rows':rows,'limitation':'Only provenance and numeric constraints transmitted to provider verified; final business, symptoms, advice and other goals need full reply review.'}
with (R/'envelope-verification.json').open('x',encoding='utf8') as f:json.dump(out,f,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in out.items() if k!='rows'}))
