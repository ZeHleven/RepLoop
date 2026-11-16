"""Spend at most one further HTTP attempt, retaining the original batch's occupancy."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from agent_eval import source_manifest as shared_observer_manifest
SOURCE = ROOT / '.tmp/agent-next-workout-prep-20261110'
EVIDENCE = ROOT / 'artifacts/agent-dev-notes/next-workout-prep-20261110'
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
assert head != '415fe17387b8844f20622deaf194f90e02bcf67b'
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
tested = json.loads((EVIDENCE / 'ci3/source-manifest.json').read_text(encoding='utf-8'))
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == h for p, h in tested.items())
assert json.loads((EVIDENCE / 'ci3/result.json').read_text(encoding='utf-8'))['exit_code'] == 0
prior = json.loads((EVIDENCE / 'live-01/review.json').read_text(encoding='utf-8'))
assert prior['provider_http_attempts'] == 9 and prior['total_budget_occupancy_cny'] == 0.162664
original_auth = json.loads((EVIDENCE / 'live-preregistration.json').read_text(encoding='utf-8'))
assert original_auth['authorization'] == '允许，进行真实模型复测'
assert original_auth['max_http_requests'] == 30 and original_auth['max_estimated_cny'] == 0.5

assert not (EVIDENCE / 'live-02-answer/provider-events.jsonl').exists(), 'The preflight failure must have sent no HTTP.'
target = EVIDENCE / 'live-03-answer'
target.mkdir(exist_ok=False)
runner = Path(__file__).with_name('replay_live_answer.py')
registration = {'candidate': head, 'authorization': original_auth['authorization'],
    'max_http_requests': 1, 'max_estimated_cny': 0.337336, 'prior_calls': 9,
    'prior_known_estimated_cny': 0.044696, 'prior_unknown_reserved_cny': 0.117968,
    'total_max_http_requests': 30, 'total_max_estimated_cny': 0.5,
    'scope': 'one real final-answer replay after interface fix; not a second full batch, not full-task or phone acceptance',
    'shared_observer_files': shared_observer_manifest(),
    'runner_sha256': hashlib.sha256(runner.read_bytes()).hexdigest(),
    'original_result_sha256': hashlib.sha256((EVIDENCE / 'live-01/result.json').read_bytes()).hexdigest(),
    'deploy_authorized': False}
(target / 'preregistration.json').write_text(json.dumps(registration, ensure_ascii=False, indent=2), encoding='utf-8')
settings = dotenv_values(ROOT / '.env')
env = os.environ.copy()
passed = []
for name in ['DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_CHAT_MODEL', 'DEEPSEEK_REASONING_MODEL', 'AGENT_MODEL', 'AGENT_INTENT_MODEL']:
    if settings.get(name):
        env[name] = settings[name]
        passed.extend(['-e', name])
assert env.get('DEEPSEEK_API_KEY')
env['DATABASE_URL'] = 'postgresql+asyncpg://fitness:prep-live-only@fitness-prep-live-20261110-pg:5432/prep_live_test'
env['SECRET_KEY'] = 'isolated-prep-live-test-secret-not-for-production'
cmd = ['docker', 'run', '--rm', '--name', 'fitness-prep-live-answer', '--network', 'fitness-experience-20261108',
    '--cpus', '2', '--memory', '2g', '-v', f'{SOURCE}:/source:ro', '-v', f'{runner.parent}:/harness:ro',
    '-v', f'{ROOT / "scripts/agent_eval"}:/eval_support/agent_eval:ro',
    '-v', f'{runner.parent / "observer"}:/observer:ro', '-v', f'{EVIDENCE / "live-01"}:/original:ro',
    '-v', f'{target}:/out', '-w', '/source/backend', '-e', 'DATABASE_URL', '-e', 'SECRET_KEY', *passed,
    '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONUNBUFFERED=1', '-e', 'PYTHONPATH=/source/backend:/eval_support',
    '--entrypoint', 'python', 'fitness-agent-backend-ci:0.5.36-local', '/harness/replay_live_answer.py']
with (target / 'console.log').open('w', encoding='utf-8') as log:
    proc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)
(target / 'exit.json').write_text(json.dumps({'exit_code': proc.returncode, 'command': cmd}, indent=2), encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
print((target / 'console.log').read_text(encoding='utf-8')[-3000:])
raise SystemExit(proc.returncode)
