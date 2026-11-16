"""Post-run persistence checks only; no provider key or performance samples."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-query-fix-20261108'
BASE = ROOT / 'artifacts/agent-dev-notes/query-history-fix-20261108'
OBSERVER = Path(__file__).parent / 'observer'

for batch, database in [('live-core', 'query_fix_core_test'), ('live-c2-core', 'query_fix_c2_core_test'), ('live-c2-extra', 'query_fix_c2_extra_test')]:
    out = BASE / batch
    assert (out / 'exit.json').exists()
    if (out / 'persistence-verification.json').exists():
        continue
    command = ['docker', 'run', '--rm', '--name', 'fitness-query-fix-verify', '--network', 'fitness-experience-20261108',
        '--cpus', '2', '--memory', '2g', '-v', f'{SOURCE}:/source:ro', '-v', f'{OBSERVER}:/observer:ro', '-v', f'{out}:/out',
        '-w', '/source/backend', '-e', f'DATABASE_URL=postgresql+asyncpg://fitness:local-experience-only@fitness-experience-20261108-pg:5432/{database}',
        '-e', 'SECRET_KEY=isolated-query-fix-verification-not-for-production', '-e', 'DEEPSEEK_API_KEY=',
        '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'PYTHONPATH=/source/backend', '--entrypoint', 'python',
        'fitness-agent-backend-ci:0.5.36-local', '/observer/verify_persistence.py']
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
    (out / 'persistence-console.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    print(json.dumps({'batch': batch, 'exit_code': result.returncode, 'summary': result.stdout.strip()}), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
