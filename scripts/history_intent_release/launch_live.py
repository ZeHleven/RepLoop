"""One attempt per frozen suite, aggregate budget, .env model key only."""
import argparse
import json
import os
from pathlib import Path
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-history-intent-20261108'
ARTIFACTS = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
OBSERVER = Path(__file__).parent / 'observer'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('suite', choices=('core', 'extra'))
    parser.add_argument('--candidate', type=int, choices=(2,), default=2)
    args = parser.parse_args()
    candidate = json.loads((ARTIFACTS / 'candidate.json').read_text(encoding='utf-8'))
    sha = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
    assert sha == candidate['commit']
    assert not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain'], text=True).strip()
    used_nano, attempts = 0, 0
    for result_path in ARTIFACTS.glob('live-*/result.json'):
        result = json.loads(result_path.read_text(encoding='utf-8'))
        assert not result['stopped'], 'prior suite stopped; inspect before any more model calls'
        for call in result['provider_calls']:
            attempts += 1
            used_nano += call['cost_nano'] if call['usage_complete'] else call['reserved_nano']
    remaining_nano = 3_000_000_000 - used_nano
    remaining_attempts = 220 - attempts
    assert remaining_nano > 0 and remaining_attempts > 0
    label = f'c{args.candidate}-{args.suite}'
    out = ARTIFACTS / ('live-' + label)
    out.mkdir(exist_ok=False)
    env = os.environ.copy()
    env['DEEPSEEK_API_KEY'] = dotenv_values(ROOT / '.env').get('DEEPSEEK_API_KEY') or ''
    assert env['DEEPSEEK_API_KEY']
    env['DATABASE_URL'] = f'postgresql+asyncpg://fitness:local-experience-only@fitness-experience-20261108-pg:5432/history_intent_c{args.candidate}_{args.suite}_test'
    env['SECRET_KEY'] = 'isolated-query-fix-test-secret-20261108-never-for-production'
    contract = 'tasks-core.json' if args.suite == 'core' else 'tasks-extra.json'
    command = ['docker', 'run', '--rm', '--name', f'fitness-history-intent-live-{label}',
        '--network', 'fitness-experience-20261108', '--cpus', '2', '--memory', '2g',
        '-v', f'{SOURCE}:/source:ro', '-v', f'{OBSERVER}:/observer:ro', '-v', f'{out}:/out', '-w', '/source/backend',
        '-v', f'{ROOT / "scripts/agent_eval"}:/eval_support/agent_eval:ro',
        '-e', 'DATABASE_URL', '-e', 'SECRET_KEY', '-e', 'DEEPSEEK_API_KEY', '-e', 'PYTHONDONTWRITEBYTECODE=1',
        '-e', 'PYTHONUNBUFFERED=1', '-e', 'PYTHONPATH=/source/backend:/eval_support', '--entrypoint', 'python',
        'fitness-agent-backend-ci:0.5.36-local', '/observer/run_baseline.py', '--source', '/source', '--commit', sha,
        '--tasks', f'/observer/{contract}', '--out', '/out', '--mode', 'live',
        '--max-cny', str(remaining_nano / 1e9), '--max-requests', str(remaining_attempts)]
    with (out / 'console.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            log.write(line)
            log.flush()
            if line.startswith(('serial-', 'concurrent_2-', '{"tasks"')):
                print(line.rstrip(), flush=True)
        code = process.wait()
    (out / 'exit.json').write_text(json.dumps({'exit_code': code, 'source_commit': sha, 'suite': args.suite}), encoding='utf-8')
    print(json.dumps({'exit_code': code, 'suite': args.suite}), flush=True)
    raise SystemExit(code)


if __name__ == '__main__':
    main()
