"""Exactly one authorized live batch, with hard request/cost reservations below transport."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from agent_eval import source_manifest as shared_observer_manifest
SOURCE=ROOT/'.tmp/agent-prep-advice-20261110'
OUT=ROOT/'artifacts/agent-dev-notes/preparation-advice-quality-20261110'
OBS=Path(__file__).parent/'observer'
auth=json.loads((OUT/'live-preregistration.json').read_text(encoding='utf-8'))
assert auth['prior_calls']==10 and auth['prior_known_estimated_nano']==51146000 and auth['prior_unknown_occupancy_nano']==117968000
assert auth['max_http_requests']==20 and auth['max_estimated_nano']==330886000
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()==auth['candidate']
assert not subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE).strip()
assert all(hashlib.sha256((OBS/p).read_bytes()).hexdigest()==h for p,h in auth['files'].items())
assert auth.get("shared_observer_files") == shared_observer_manifest(), "shared observer changed; create a new freeze"
tested=json.loads((OUT/'ci/source-manifest.json').read_text(encoding='utf-8'))
assert all(hashlib.sha256((SOURCE/p).read_bytes()).hexdigest()==h for p,h in tested.items())
assert json.loads((OUT/'ci/result.json').read_text(encoding='utf-8'))['exit_code']==0
target=OUT/'live-01';target.mkdir(exist_ok=False)
settings=dotenv_values(ROOT/'.env');env=os.environ.copy()
allowed=['DEEPSEEK_API_KEY','DEEPSEEK_BASE_URL','DEEPSEEK_CHAT_MODEL','DEEPSEEK_REASONING_MODEL','AGENT_MODEL','AGENT_INTENT_MODEL']
passed=[]
for name in allowed:
    if settings.get(name):env[name]=settings[name];passed.extend(['-e',name])
assert env.get('DEEPSEEK_API_KEY')
env['DATABASE_URL']='postgresql+asyncpg://fitness:advice-live-only@fitness-advice-live-20261110-pg:5432/advice_live_test'
env['SECRET_KEY']='isolated-prep-live-test-secret-not-for-production'
command=['docker','run','--rm','--name','fitness-advice-live-01','--network','fitness-experience-20261108',
    '--cpus','2','--memory','2g','-v',f'{SOURCE}:/source:ro','-v',f'{OBS}:/observer:ro','-v',f'{target}:/out',
    '-v', f'{ROOT / "scripts/agent_eval"}:/eval_support/agent_eval:ro',
    '-w','/source/backend','-e','DATABASE_URL','-e','SECRET_KEY',*passed,
    '-e','PYTHONDONTWRITEBYTECODE=1','-e','PYTHONUNBUFFERED=1','-e','PYTHONPATH=/source/backend:/eval_support',
    '--entrypoint','python','fitness-agent-backend-ci:0.5.36-local','/observer/run_baseline.py',
    '--source','/source','--commit',auth['candidate'],'--tasks','/observer/tasks.json','--out','/out',
    '--mode','live','--max-cny','0.330886','--max-requests','20']
sys.stdout.reconfigure(encoding='utf-8')
with (target/'console.log').open('w',encoding='utf-8') as log:
    process=subprocess.Popen(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    for line in process.stdout:
        log.write(line);log.flush()
        if line.startswith(('serial-','{"tasks"')): print(line.rstrip(),flush=True)
    code=process.wait()
(target/'exit.json').write_text(json.dumps({'exit_code':code,'source_commit':auth['candidate'],'command':command},indent=2),encoding='utf-8')
print(json.dumps({'exit_code':code}),flush=True)
raise SystemExit(code)
