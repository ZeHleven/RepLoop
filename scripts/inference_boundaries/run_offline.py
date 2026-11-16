"""Credential-free checks of the isolated inference-boundary candidate."""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-inference-boundaries-20261115'
BASE = ROOT / 'artifacts/agent-inference-boundaries-20261115'
parser = argparse.ArgumentParser()
parser.add_argument('label', choices=['before', 'before-corrected', 'after', 'targeted', 'ci'])
args = parser.parse_args()
out = BASE / args.label
out.mkdir(parents=True, exist_ok=False)
manifest = {p.relative_to(SOURCE).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted((SOURCE / 'backend').rglob('*')) if p.is_file() and '__pycache__' not in p.parts}
(out / 'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
db = 'inference_boundary_ci_test' if args.label == 'ci' else 'inference_boundary_test'
url = f'postgresql+asyncpg://fitness:inference-boundary-local-only@127.0.0.1:5432/{db}'
command = ['docker', 'run', '--rm', '--name', 'fitness-inference-boundaries-' + args.label,
    '--network', 'container:fitness-inference-boundaries-20261115-pg', '--cpus', '2', '--memory', '2g',
    '-v', f'{SOURCE}:/source:ro', '-v', f'{out}:/out',
    '-v', f'{ROOT / "scripts/inference_boundaries"}:/harness:ro', '-w', '/source/backend',
    '-e', f'DATABASE_URL={url}', '-e', f'TEST_DATABASE_URL={url}',
    '-e', f'AGENT_QUERY_EVAL_DATABASE_URL={url}',
    '-e', 'SECRET_KEY=isolated-inference-boundary-tests-not-for-production',
    '-e', 'DEEPSEEK_API_KEY=', '-e', 'EMBEDDING_API_KEY=',
    '-e', 'PREP_EVIDENCE_DIR=/out', '-e', 'PYTHONPATH=/source/backend',
    '-e', 'PYTHONDONTWRITEBYTECODE=1', '--entrypoint', 'python',
    'fitness-agent-backend-ci:0.5.36-local']
if args.label == 'ci':
    command += ['/harness/checks.py']
else:
    tests = ['tests/test_training_advice_inference_boundaries.py', 'tests/test_preparation_rest_scope.py']
    if args.label == 'targeted':
        tests += ['tests/test_preparation_advice_contract.py', 'tests/test_agent_runtime_v1.py',
                  'tests/test_required_read_completion.py', 'tests/test_agent_planner.py',
                  'tests/test_agent_tools.py']
    command += ['-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider', *tests,
                '--junitxml=/out/tests.xml']
with (out / 'runner.log').open('w', encoding='utf-8') as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
(out / 'result.json').write_text(json.dumps({'command': command, 'exit_code': result.returncode}, indent=2), encoding='utf-8')
print((out / 'runner.log').read_text(encoding='utf-8')[-6000:])
raise SystemExit(result.returncode)
