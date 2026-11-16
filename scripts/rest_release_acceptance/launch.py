"""One launch per registered phase, debiting all earlier HTTP usage first."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

from dotenv import dotenv_values
from phase_budget import remaining_budget

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from agent_eval import source_manifest as shared_observer_manifest
SOURCE = ROOT / '.tmp/agent-rest-scope-20261111'
OUT = ROOT / 'artifacts/agent-dev-notes/rest-release-acceptance-20261111'
HARNESS = Path(__file__).parent
parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['preparation', 'formal'])
args = parser.parse_args()
auth = json.loads((OUT / 'authorization.json').read_text(encoding='utf-8'))
freeze = json.loads((OUT / 'preflight-freeze.json').read_text(encoding='utf-8'))
assert auth['user_authorized'] and auth['max_http_requests'] == 300 and auth['max_estimated_nano'] == 4_000_000_000
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() == auth['candidate']
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
assert all(hashlib.sha256((HARNESS / p).read_bytes()).hexdigest() == h for p, h in freeze['harness_files'].items())
assert freeze.get("shared_observer_files") == shared_observer_manifest(), "shared observer changed; create a new freeze"
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == h for p, h in freeze['source_files'].items())
assert datetime.now(timezone(timedelta(hours=8))).date().isoformat() == freeze['business_date']
assert json.loads((OUT / 'fixture-preflight/fixture-check.json').read_text(encoding='utf-8'))['passed']
suite = ET.parse(OUT / 'offline-guard.xml').getroot()
assert len(suite.findall('.//testcase')) == 10 and not suite.findall('.//failure') and not suite.findall('.//error')
prior = []
if args.stage == 'formal':
    previous = OUT / 'live-preparation-01'
    assert json.loads((previous / 'exit.json').read_text(encoding='utf-8'))['exit_code'] == 0
    previous_result = json.loads((previous / 'result.json').read_text(encoding='utf-8'))
    assert previous_result['source_commit'] == auth['candidate']
    assert previous_result['business_date'] == freeze['business_date']
    assert all(t['collection_complete'] for t in previous_result['tasks'])
    assert json.loads((previous / 'ledger.json').read_text(encoding='utf-8'))['pending_runs'] == 0
    prior.append(previous_result)
allowance = remaining_budget(prior)
target = OUT / f'live-{args.stage}-01'
target.mkdir(exist_ok=False)
(target / 'phase-allowance.json').write_text(json.dumps(allowance, indent=2) + '\n', encoding='utf-8')
env = os.environ.copy()
values = dotenv_values(ROOT / '.env')
passed = []
for name in ['DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_CHAT_MODEL', 'DEEPSEEK_REASONING_MODEL', 'AGENT_MODEL', 'AGENT_INTENT_MODEL']:
    if values.get(name):
        env[name] = values[name]
        passed.extend(['-e', name])
assert env.get('DEEPSEEK_API_KEY')
database = 'rest_acceptance_tasks_test' if args.stage == 'preparation' else 'rest_acceptance_formal_test'
env['DATABASE_URL'] = f'postgresql+asyncpg://fitness:rest-acceptance-local-only@127.0.0.1:5432/{database}'
env['SECRET_KEY'] = 'isolated-preparation-release-not-production-secret'
env['ACCEPTANCE_BUSINESS_DATE'] = freeze['business_date']
command = ['docker', 'run', '--rm', '--name', f'fitness-rest-acceptance-{args.stage}',
           '--network', 'container:fitness-rest-acceptance-20261111-pg', '--cpus', '2', '--memory', '2g',
           '-v', f'{SOURCE}:/source:ro', '-v', f'{HARNESS}:/harness:ro', '-v', f'{target}:/out',
           '-v', f'{ROOT / "scripts/agent_eval"}:/eval_support/agent_eval:ro',
           '-w', '/source/backend', '-e', 'DATABASE_URL', '-e', 'SECRET_KEY', '-e', 'ACCEPTANCE_BUSINESS_DATE', *passed,
           '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONUNBUFFERED=1', '-e', 'PYTHONPATH=/source/backend:/harness:/eval_support',
           '--entrypoint', 'python', 'fitness-agent-backend-ci:0.5.36-local',
           '/harness/observer/run_baseline.py' if args.stage == 'preparation' else '/harness/formal_gate.py',
           '--source', '/source', '--commit', auth['candidate'], '--out', '/out',
           '--max-requests', str(allowance['remaining_http']),
           '--max-cny', f"{allowance['remaining_nano'] / 1e9:.9f}"]
if args.stage == 'preparation':
    command += ['--mode', 'live', '--tasks', '/harness/observer/tasks.json']
sys.stdout.reconfigure(encoding='utf-8')
with (target / 'console.log').open('w', encoding='utf-8') as log:
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace')
    for line in process.stdout:
        log.write(line)
        log.flush()
        if line.startswith(('PROGRESS', 'serial-', '{"')):
            print(line.rstrip(), flush=True)
    code = process.wait()
(target / 'exit.json').write_text(json.dumps({'exit_code': code, 'command': command,
    'source_commit': auth['candidate']}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'stage': args.stage, 'exit_code': code}), flush=True)
raise SystemExit(code)
