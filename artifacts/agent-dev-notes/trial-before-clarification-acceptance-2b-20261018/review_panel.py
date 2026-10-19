"""Print all new final-answer paragraphs once; repeats reference reviewed text.
This is a reading aid, never a scorer. Full responses remain in runs/*.json.
"""
from pathlib import Path
import json,sys,hashlib
R=Path(__file__).resolve().parent;batch=sys.argv[1]
memo_path=R/'reading-progress.json'
memo=json.loads(memo_path.read_text(encoding='utf8')) if memo_path.exists() else {'paragraphs':{},'instances':[]}
for p in sorted((R/batch/'runs').glob('*.json')):
    if len(sys.argv)>2 and p.stem not in sys.argv[2:]:continue
    key=batch+'/'+p.stem
    if key in memo['instances']:continue
    r=json.loads(p.read_text(encoding='utf8'))
    if not r.get('finished_at'):continue
    print('\nINSTANCE',key,'snapshots_unchanged',r['baseline']==r['final'])
    for t in r['turns']:
        b=t['body'];trace=b.get('execution_trace') or {};d=trace.get('training_decision') or {}
        print('USER',t['message']);print('ERROR',b.get('error_code'),'decision',d.get('status'),'repair',d.get('repair_attempts'),'errors',d.get('errors'))
        if d:print('PROPOSAL',json.dumps(d.get('proposal'),ensure_ascii=False))
        for text in (b.get('reply') or '').split('\n\n'):
            h=hashlib.sha256(text.encode()).hexdigest()
            if h not in memo['paragraphs']:
                ident='P'+str(len(memo['paragraphs'])+1);memo['paragraphs'][h]={'id':ident,'text':text}
                print(ident,'NEW',text)
            else:print(memo['paragraphs'][h]['id'],'same')
    print('TOOLS',[(t['turn'],t['tool'],t['status']) for t in r['tool_attempts']])
    memo['instances'].append(key)
memo_path.write_text(json.dumps(memo,ensure_ascii=False,indent=2),encoding='utf8')
