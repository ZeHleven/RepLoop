"""Validate synthetic SQL prerequisites without model keys, then freeze a run."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from agent_eval import source_manifest as shared_observer_manifest
HARNESS = Path(__file__).parent
SOURCE = ROOT / '.tmp/agent-rest-scope-20261111'
OUT = ROOT / 'artifacts/agent-dev-notes/rest-release-acceptance-20261111'
auth = json.loads((OUT / 'authorization.json').read_text(encoding='utf-8'))
day = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
assert not (OUT / 'preflight-freeze.json').exists()
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() == auth['candidate']
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
suite = ET.parse(OUT / 'offline-guard.xml').getroot()
assert len(suite.findall('.//testcase')) == 10 and not suite.findall('.//failure') and not suite.findall('.//error')
for phase, db in [('fixture', 'rest_acceptance_fixture_test'), ('formal', 'rest_acceptance_formal_test')]:
    target = OUT / f'{phase}-preflight'
    target.mkdir(exist_ok=False)
    cmd = ['docker', 'run', '--rm', '--name', f'fitness-rest-acceptance-{phase}-preflight',
        '--network', 'container:fitness-rest-acceptance-20261111-pg', '--cpus', '2', '--memory', '1g',
        '-v', f'{SOURCE}:/source:ro', '-v', f'{HARNESS}:/harness:ro', '-v', f'{target}:/out',
        '-v', f'{ROOT / "scripts/agent_eval"}:/eval_support/agent_eval:ro',
        '-w', '/source/backend', '-e', 'PYTHONPATH=/source/backend:/harness:/eval_support', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'DEEPSEEK_API_KEY=', '-e', 'EMBEDDING_API_KEY=',
        '-e', f'DATABASE_URL=postgresql+asyncpg://fitness:rest-acceptance-local-only@127.0.0.1:5432/{db}',
        '-e', 'SECRET_KEY=rest-acceptance-prerequisite-not-production', '-e', f'ACCEPTANCE_BUSINESS_DATE={day}',
        '--entrypoint', 'python', 'fitness-agent-backend-ci:0.5.36-local']
    if phase == 'fixture':
        cmd += ['/harness/observer/run_baseline.py', '--source', '/source', '--tasks', '/harness/observer/tasks.json',
                '--commit', auth['candidate'], '--out', '/out', '--mode', 'fixture', '--max-cny', '0', '--max-requests', '0']
    else:
        cmd += ['/harness/prepare_formal.py']
    with (target / 'console.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    (target / 'command.json').write_text(json.dumps({'command': cmd, 'exit_code': result.returncode}, indent=2), encoding='utf-8')
    print((target / 'console.log').read_text(encoding='utf-8')[-2200:], flush=True)
    assert result.returncode == 0, phase
fixture = json.loads((OUT / 'fixture-preflight/fixture-check.json').read_text(encoding='utf-8'))
assert fixture['passed'] and fixture['http_calls'] == 0
freeze = {'candidate': auth['candidate'], 'business_date': day,
    'source_files': {p.relative_to(SOURCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((SOURCE / 'backend').rglob('*')) if p.is_file() and '__pycache__' not in p.parts},
    'harness_files': {p.relative_to(HARNESS).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(HARNESS.rglob('*')) if p.is_file() and p.suffix in {'.py', '.json'} and '__pycache__' not in p.parts},
    'shared_observer_files': shared_observer_manifest(),
    'model_http_calls_before_launch': 0, 'offline_guard_tests': 10, 'fixture_check': True}
(OUT / 'preflight-freeze.json').write_text(json.dumps(freeze, indent=2), encoding='utf-8')
print(json.dumps({'frozen_commit': auth['candidate'], 'business_date': day,
    'source_files': len(freeze['source_files']), 'harness_files': len(freeze['harness_files']), 'model_http': 0}))
