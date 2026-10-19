from pathlib import Path
import json,sys
R=Path(__file__).resolve().parent
for ident in sys.argv[2:]:
    p=R/sys.argv[1]/'runs'/(ident+'.json')
    if not p.exists():print(ident,'pending');continue
    r=json.loads(p.read_text(encoding='utf8'))
    if not r.get('finished_at'):print(ident,'pending');continue
    print(ident,'UNCHANGED',r.get('baseline')==r.get('final'),'ERRORS',r['score']['terminal_errors'])
    for t in r['turns']:
        print('USER',t['message'])
        b=t['body'];a=b.get('execution_trace',{}).get('assessment_delivery') or {}
        print('TURN',t['index'],'ERROR',b.get('error_code'),'GUARD',a.get('status'),json.dumps(a.get('findings'),ensure_ascii=False))
        print(b.get('reply'))
        if b.get('error_code'):print('REPAIR',a.get('repair_draft'),'REMAINING',json.dumps(a.get('remaining_findings'),ensure_ascii=False))
    sources=[]
    for t in r['tool_attempts']:
        d=t.get('result') or {};p=d.get('plan') or {}
        if p:d=dict(days=p.get('days_per_week'),completed_days=p.get('weekly_completed_days'),exercises=[{k:v for k,v in e.items() if k in ('exercise_name','sets','reps','rest_seconds','recommended_weight_kg')} for e in p.get('exercises',[])])
        sources.append(dict(turn=t['turn'],tool=t['tool'],status=t['status'],result=d))
    print('TOOLS',json.dumps(sources,ensure_ascii=False))
