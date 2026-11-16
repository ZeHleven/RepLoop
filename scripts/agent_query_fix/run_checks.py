"""CI-equivalent backend checks in the isolated test container; no live key."""
import json
import os
from pathlib import Path
import subprocess
import sys

source, out = Path('/source'), Path('/out')
assert not os.environ.get('DEEPSEEK_API_KEY')
assert os.environ['TEST_DATABASE_URL'].rsplit('/', 1)[-1] in {'query_fix_ci_test', 'query_fix_final_ci_test'}
steps = [
    # The reused runtime image has no git. Run the identical scanner on the host.
    ('migration-up-0032', source / 'backend', [sys.executable, '-m', 'alembic', 'upgrade', '0032']),
    ('migration-down-0023', source / 'backend', [sys.executable, '-m', 'alembic', 'downgrade', '0023']),
    ('migration-up-head', source / 'backend', [sys.executable, '-m', 'alembic', 'upgrade', 'head']),
    ('backend-tests', source / 'backend', [sys.executable, '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
        '--ignore=evals/tests/test_agent_tasks.py', '--ignore=evals/tests/test_multiturn_tasks.py',
        '--ignore=evals/tests/test_revision_tasks.py', '--junitxml=/out/backend-tests.xml']),
    ('intent-rules', source, [sys.executable, 'backend/scripts/evaluate_agent_intents.py', '--mode', 'rules', '--strict']),
    ('scripted-tasks', source, [sys.executable, 'backend/scripts/evaluate_agent_tasks.py', '--split', 'all', '--output', '/out/scripted-tasks']),
    ('multiturn', source, [sys.executable, 'backend/scripts/evaluate_multiturn_tasks.py', '--output', '/out/multiturn']),
    ('proposal-revisions', source, [sys.executable, 'backend/scripts/evaluate_revision_tasks.py', '--output', '/out/proposal-revisions']),
]
start = os.environ.get('QUERY_FIX_CHECK_START')
offset = next(i for i, row in enumerate(steps) if row[0] == start) if start else 0
results = json.loads((out / 'checks.json').read_text()) if offset else []
assert not offset or results == [{'step': name, 'exit_code': 0} for name, _, _ in steps[:offset]]
for name, cwd, command in steps[offset:]:
    print(json.dumps({'step': name, 'state': 'started'}), flush=True)
    with (out / (name + '.log')).open('w') as log:
        result = subprocess.run(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)
    results.append({'step': name, 'exit_code': result.returncode})
    (out / 'checks.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results[-1]), flush=True)
    if result.returncode:
        print((out / (name + '.log')).read_text()[-7000:], flush=True)
        raise SystemExit(result.returncode)
