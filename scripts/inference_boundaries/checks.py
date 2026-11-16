"""Run the repository's backend CI checks on a fresh, isolated database."""
from pathlib import Path
import importlib.metadata
import json
import os
import subprocess
import sys

SOURCE = Path('/source')
OUT = Path('/out')
assert os.environ['DATABASE_URL'].endswith('/inference_boundary_ci_test')
assert not os.environ.get('DEEPSEEK_API_KEY')
(OUT / 'dependencies.json').write_text(json.dumps({d.metadata['Name']: d.version
    for d in importlib.metadata.distributions()}, indent=2))
steps = [
    ('migration-up-0032', 'backend', ['-m', 'alembic', 'upgrade', '0032']),
    ('migration-down-0023', 'backend', ['-m', 'alembic', 'downgrade', '0023']),
    ('migration-up-head', 'backend', ['-m', 'alembic', 'upgrade', 'head']),
    ('backend-tests', 'backend', ['-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
        '--ignore=evals/tests/test_agent_tasks.py', '--ignore=evals/tests/test_multiturn_tasks.py',
        '--ignore=evals/tests/test_revision_tasks.py', '--junitxml=/out/backend-tests.xml']),
    ('intent-rules', '', ['backend/scripts/evaluate_agent_intents.py', '--mode', 'rules', '--strict']),
    ('scripted-tasks', '', ['backend/scripts/evaluate_agent_tasks.py', '--split', 'all', '--output', '/out/scripted-tasks']),
    ('multiturn', '', ['backend/scripts/evaluate_multiturn_tasks.py', '--output', '/out/multiturn']),
    ('proposal-revisions', '', ['backend/scripts/evaluate_revision_tasks.py', '--output', '/out/proposal-revisions']),
]
results = []
for name, cwd, arguments in steps:
    print(json.dumps({'step': name, 'status': 'started'}), flush=True)
    with (OUT / f'{name}.log').open('w') as stream:
        process = subprocess.run([sys.executable, *arguments], cwd=SOURCE / cwd,
                                 stdout=stream, stderr=subprocess.STDOUT)
    results.append({'step': name, 'exit_code': process.returncode})
    (OUT / 'checks.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results[-1]), flush=True)
    if process.returncode:
        print((OUT / f'{name}.log').read_text()[-5000:], flush=True)
        raise SystemExit(process.returncode)
