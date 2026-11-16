"""Run the isolated demo from the repository root: python scripts/run_demo.py."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8017)
    parser.add_argument('--no-migrate', action='store_true')
    args = parser.parse_args()
    config = ROOT / '.env.demo.local'
    if not config.exists():
        config.write_text(
            'DATABASE_URL=postgresql+asyncpg://demo:demo_local_only@127.0.0.1:5547/fitness_demo\n'
            f'SECRET_KEY={secrets.token_urlsafe(48)}\n'
            'DEEPSEEK_API_KEY=\nDEMO_TOTAL_BUDGET_NANO=0\n', encoding='utf-8')
        print('Created ignored .env.demo.local; live model calls are disabled until configured.', flush=True)
    from dotenv import dotenv_values
    env = {**os.environ, **{k: v for k, v in dotenv_values(config).items() if v is not None},
           'DEMO_ENABLED': 'true', 'DEMO_STATIC_DIR': str(ROOT / 'miniapp/dist-h5'),
           'RAG_ENABLED': 'false', 'PYTHONUNBUFFERED': '1'}
    if not (ROOT / 'miniapp/dist-h5/index.html').exists():
        parser.error('Build H5 first: node miniapp/scripts/build-h5.mjs')
    from package_demo import validate_h5_build
    identity = validate_h5_build()
    (ROOT / 'backend/app/build_metadata.json').write_text(
        json.dumps(identity, indent=2) + '\n', encoding='utf-8')
    # Refuse a production database before even attempting migrations.
    from urllib.parse import urlsplit
    database = urlsplit(env.get('DATABASE_URL', '')).path.lstrip('/')
    if not database.endswith('_demo'):
        parser.error('DATABASE_URL must name a separate database ending in _demo')
    if not args.no_migrate:
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT / 'backend', env=env, check=True)
        for script in ('seed_exercises.py', 'seed_foods.py'):
            subprocess.run([sys.executable, f'scripts/{script}'], cwd=ROOT / 'backend', env=env, check=True)
    print(f'Demo: http://127.0.0.1:{args.port}', flush=True)
    return subprocess.call([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(args.port)], cwd=ROOT / 'backend', env=env)


if __name__ == '__main__':
    raise SystemExit(main())
