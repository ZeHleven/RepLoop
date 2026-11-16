"""Keep both failure and repaired offline gate transcripts."""
import argparse
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-release-route-billing-20261108'
OUT = ROOT / 'artifacts/agent-dev-notes/release-route-billing-20261108'
parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['before', 'after'])
args = parser.parse_args()
tests = ['tests/test_route_eval_billing.py']
if args.stage == 'after':
    tests += ['tests/test_agent_routing_live_eval.py', 'tests/test_daily_meal_live_eval.py', 'tests/test_model_budget.py']
url = 'postgresql+asyncpg://fitness:local-experience-only@127.0.0.1:5432/history_route_gate_test'
command = ['docker', 'run', '--rm', '--name', 'fitness-route-billing-' + args.stage,
    '--network', 'container:fitness-experience-20261108-pg', '--cpus', '2', '--memory', '2g',
    '-v', f'{SOURCE}:/source:ro', '-v', f'{OUT}:/out', '-w', '/source/backend',
    '-e', 'DATABASE_URL=' + url, '-e', 'TEST_DATABASE_URL=' + url,
    '-e', 'SECRET_KEY=isolated-route-billing-not-production', '-e', 'PYTHONPATH=/source/backend',
    '-e', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', 'python', 'fitness-agent-backend-ci:0.5.36-local',
    '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', *tests,
    '--junitxml=/out/' + args.stage + '.xml']
log = OUT / (args.stage + '.log')
assert not log.exists(), 'Preserve prior attempts.'
with log.open('w', encoding='utf-8') as stream:
    result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
(OUT / (args.stage + '-command.json')).write_text(json.dumps({'command': command, 'exit_code': result.returncode}, indent=2), encoding='utf-8')
print(log.read_text(encoding='utf-8')[-6000:])
raise SystemExit(result.returncode)
