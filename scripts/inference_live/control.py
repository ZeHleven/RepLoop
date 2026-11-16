"""Freeze, launch once, and inspect one isolated eight-scenario acceptance."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from agent_eval import source_manifest as shared_observer_manifest

SOURCE = ROOT / '.tmp/agent-inference-boundaries-20261115'
HARNESS = Path(__file__).parent
OUT = ROOT / 'artifacts/agent-inference-live-20261115'
CONTAINER = 'fitness-inference-live-20261115-pg'
IMAGE = 'fitness-agent-backend-ci:0.5.36-local'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def manifest(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}


def verify_candidate():
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() == auth['candidate']
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
    verified = read(ROOT / 'artifacts/agent-inference-boundaries-20261115/ci/source-manifest.json')
    assert all(hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == value for name, value in verified.items())


def command(target, database, network):
    return ['docker', 'run', '--rm', '--name', 'fitness-inference-live-' + target.name,
            '--network', network, '--cpus', '2', '--memory', '2g',
            '-v', f'{SOURCE}:/source:ro', '-v', f'{HARNESS}:/harness:ro',
            '-v', f'{ROOT / "scripts/agent_eval"}:/eval_support/agent_eval:ro', '-v', f'{target}:/out',
            '-w', '/source/backend', '-e', f'DATABASE_URL=postgresql+asyncpg://fitness:inference-live-local-only@127.0.0.1:5432/{database}',
            '-e', 'SECRET_KEY=inference-live-synthetic-isolation-secret',
            '-e', 'EMBEDDING_API_KEY=', '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONUNBUFFERED=1',
            '-e', 'PYTHONPATH=/source/backend:/harness:/eval_support']


def execute(target, cmd, env=None):
    write(target / 'command.json', {'command': cmd, 'candidate': auth['candidate']})
    with (target / 'console.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            log.write(line)
            log.flush()
            if line.startswith(('serial-', '{"', 'PROGRESS')):
                print(line.rstrip(), flush=True)
        code = process.wait()
    write(target / 'exit.json', {'exit_code': code, 'candidate': auth['candidate']})
    if code:
        print((target / 'console.log').read_text(encoding='utf-8')[-3500:])
        raise SystemExit(code)


parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['guard', 'preflight', 'live', 'postcheck'])
args = parser.parse_args()
auth = read(OUT / 'authorization.json')
assert auth['user_authorized_live_scenarios']
assert auth['max_http_requests'] == 60 and auth['max_estimated_nano'] == 1_000_000_000
verify_candidate()

if args.stage == 'guard':
    target = OUT / 'offline-guard'
    target.mkdir(exist_ok=False)
    cmd = command(target, 'inference_live_fixture_test', 'none') + [
        '-e', 'DEEPSEEK_API_KEY=', '--entrypoint', 'python', IMAGE, '-m', 'pytest',
        '/harness/test_guard.py',
        '-q', '--tb=short', '-p', 'no:cacheprovider', '--junitxml=/out/tests.xml']
    execute(target, cmd)
    print((target / 'console.log').read_text()[-2000:])
    raise SystemExit(0)

if args.stage == 'preflight':
    assert not (OUT / 'preflight-freeze.json').exists()
    guard = ET.parse(OUT / 'offline-guard/tests.xml').getroot()
    assert len(guard.findall('.//testcase')) == 4 and not guard.findall('.//failure') and not guard.findall('.//error')
    planned = read(OUT / 'planned-cases-before.json')
    tasks = read(HARNESS / 'tasks.json')
    assert [c['turns'] for c in planned['cases']] == [c['turns'] for c in tasks['cases']]
    assert [c['id'] for c in tasks['cases']] == [f'I{i:02d}' for i in range(1, 9)]
    assert sum(len(c['turns']) for c in tasks['cases']) == 10
    assert not any(c.get('confirm') for c in tasks['cases'])
    day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    target = OUT / 'fixture-preflight'
    target.mkdir(exist_ok=False)
    cmd = command(target, 'inference_live_fixture_test', 'container:' + CONTAINER) + [
        '-e', 'DEEPSEEK_API_KEY=', '-e', 'ACCEPTANCE_BUSINESS_DATE=' + day, '--entrypoint', 'python', IMAGE,
        '/harness/run.py', '--source', '/source', '--tasks', '/harness/tasks.json', '--commit', auth['candidate'],
        '--out', '/out', '--mode', 'fixture', '--max-cny', '0', '--max-requests', '0']
    execute(target, cmd)
    fixture = read(target / 'fixture-check.json')
    assert fixture['passed'] and fixture['http_calls'] == 0
    freeze = {'candidate': auth['candidate'], 'business_date': day,
              'source_files': manifest(SOURCE / 'backend'), 'harness_files': manifest(HARNESS),
              'shared_observer_files': shared_observer_manifest(),
              'offline_guard_tests': len(guard.findall('.//testcase')), 'fixture_check': True,
              'model_http_before_launch': 0}
    write(OUT / 'preflight-freeze.json', freeze)
    print(json.dumps({'frozen_candidate': auth['candidate'], 'business_date': day, 'cases': 8, 'model_http': 0}))
    raise SystemExit(0)

freeze = read(OUT / 'preflight-freeze.json')
assert freeze['source_files'] == manifest(SOURCE / 'backend')
assert freeze['harness_files'] == manifest(HARNESS)
assert freeze['shared_observer_files'] == shared_observer_manifest()

if args.stage == 'postcheck':
    target = OUT / 'postcheck'
    target.mkdir(exist_ok=False)
    cmd = command(target, 'inference_live_tasks_test', 'container:' + CONTAINER) + [
        '-e', 'DEEPSEEK_API_KEY=', '--entrypoint', 'python', IMAGE, '/harness/postcheck.py']
    execute(target, cmd)
    raise SystemExit(0)

assert datetime.now(timezone(timedelta(hours=8))).date().isoformat() == freeze['business_date']
target = OUT / 'live-01'
# Exclusive directory creation prevents resetting the allowance or selecting a retry.
target.mkdir(exist_ok=False)
env = os.environ.copy()
values = dotenv_values(ROOT / '.env')
passed = []
for name in ['DEEPSEEK_API_KEY', 'DEEPSEEK_BASE_URL', 'DEEPSEEK_CHAT_MODEL', 'DEEPSEEK_REASONING_MODEL', 'AGENT_MODEL', 'AGENT_INTENT_MODEL']:
    if values.get(name):
        env[name] = values[name]
        passed.extend(['-e', name])
assert env.get('DEEPSEEK_API_KEY')
cmd = command(target, 'inference_live_tasks_test', 'container:' + CONTAINER) + passed + [
    '-e', 'ACCEPTANCE_BUSINESS_DATE=' + freeze['business_date'], '--entrypoint', 'python', IMAGE,
    '/harness/run.py', '--source', '/source', '--tasks', '/harness/tasks.json', '--commit', auth['candidate'],
    '--out', '/out', '--mode', 'live', '--max-requests', '60', '--max-cny', '1.000000000']
execute(target, cmd, env)
