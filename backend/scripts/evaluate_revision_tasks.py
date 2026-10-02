"""Complete proposal-revision/query regression, with immutable attempt records."""
import argparse,json,os,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
import hashlib
BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from evals.postgres_test_fixtures import query_eval_database_url
from evaluate_agent_tasks import source_fingerprint

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--mode',choices=['scripted','live'],default='scripted')
    args=parser.parse_args();query_eval_database_url()
    if args.output.exists():parser.error('Use a fresh attempt directory')
    args.output.mkdir(parents=True)
    env=dict(os.environ,REVISION_EVAL_DIR=str(args.output.resolve()),REVISION_EVAL_MODE=args.mode,PYTHONPATH=str(BACKEND),PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    if args.mode=='scripted':env['DEEPSEEK_API_KEY']=''
    command=[sys.executable,'-m','pytest','--noconftest','-c',str(BACKEND/'pytest.ini'),'-p','evals.task_fixtures','-p','no:cacheprovider',str(BACKEND/'evals/tests/test_revision_tasks.py'),'-q','--tb=short']
    metadata={'created_at':datetime.now(timezone.utc).isoformat(),'source':source_fingerprint(),'matrix_sha256':hashlib.sha256((BACKEND/'evals/revision_cases.json').read_bytes()).hexdigest(),'command':command,'mode':args.mode}
    (args.output/'metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    with (args.output/'pytest.log').open('w',encoding='utf-8') as log:
        try:code=subprocess.run(command,cwd=BACKEND.parent,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200).returncode
        except subprocess.TimeoutExpired:code=124
    cases=[]
    for case in json.loads((BACKEND/'evals/revision_cases.json').read_text(encoding='utf-8'))['cases']:
        path=args.output/(case['id']+'.json');row=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        cases.append({'id':case['id'],'passed':row.get('passed') is True and row.get('mode')==args.mode,'checks':row.get('checks',[]),'error':row.get('error'),'queue_time_reversed':row.get('queue_time_reversed'),'clarification_runs':row.get('clarification_runs'),'turn_wait_ms':[t['wait_ms'] for t in row.get('turns',[])]})
    report={'cases':cases,'pytest_exit_code':code,'passed_gate':code==0 and all(c['passed'] for c in cases),'mode':args.mode,'scope':'8 synthetic API/worker/SQL tasks; no UI or production success-rate claim'}
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (args.output/'failure-inbox.json').write_text(json.dumps([dict(c,classification='untriaged') for c in cases if not c['passed']],ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False));return 0 if report['passed_gate'] else 1
if __name__=='__main__':raise SystemExit(main())
