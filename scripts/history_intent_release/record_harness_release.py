"""Keep the failed release and the corrected harness release separate."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/release-route-billing-20261108'
MAIN = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
SOURCE = ROOT / '.tmp/agent-release-route-billing-20261108'
parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['open', 'ci', 'merge', 'gate', 'main_ci'])
parser.add_argument('--run', type=int)
args = parser.parse_args()
env = os.environ.copy()
env.update(HTTP_PROXY='http://127.0.0.1:7897', HTTPS_PROXY='http://127.0.0.1:7897')

def gh(*params):
    return json.loads(subprocess.check_output(['gh', *params], env=env, text=True, encoding='utf-8'))

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

status = read(MAIN / 'release-status.json')
original = MAIN / 'release-status-a65a3ff-before-harness.json'
if not original.exists():
    write(original, status)
receipt = status.get('route_harness_repair', {'local_provider_calls': 0, 'targeted_passed': 50, 'before_failed': 5})
pr = gh('pr', 'view', '47', '--repo', 'ZeHleven/RepLoop', '--json', 'number,url,state,headRefOid,baseRefOid,mergeStateStatus,mergedAt,mergeCommit,statusCheckRollup')
write(OUT / ('pr-' + args.stage + '.json'), pr)
receipt['pr'] = pr
runtime = read(OUT / 'source-verification.json')
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == value for p, value in runtime['runtime_files'].items())
receipt['runtime_files_unchanged'] = len(runtime['runtime_files'])
log_path = OUT / 'github-ci-backend.log'
if log_path.exists():
    match = re.search(r'(\d+) passed, (\d+) warnings', log_path.read_text(encoding='utf-8'))
    if match:
        receipt['github_backend_tests_passed'] = int(match[1])
        receipt['github_backend_warnings'] = int(match[2])
        receipt['github_backend_log_sha256'] = hashlib.sha256(log_path.read_bytes()).hexdigest()
if args.stage == 'merge':
    assert pr['state'] == 'MERGED'
    status['prior_failed_formal_gate'] = read(original).get('formal_gate_verified')
    status['release_candidate_sha'] = pr['mergeCommit']['oid']
    status['package_source_path'] = str(SOURCE.relative_to(ROOT)).replace('\\', '/')
    status['formal_gate_artifacts_dir'] = str((OUT / 'formal-gate').relative_to(ROOT)).replace('\\', '/')
    status.pop('formal_gate_verified', None)
    status.pop('gate_run', None)
if args.run:
    run = gh('api', f'repos/ZeHleven/RepLoop/actions/runs/{args.run}')
    jobs = gh('api', f'repos/ZeHleven/RepLoop/actions/runs/{args.run}/jobs')
    run_receipt = {k: run[k] for k in ['id', 'event', 'head_sha', 'name', 'status', 'conclusion', 'run_attempt', 'created_at', 'updated_at']}
    run_receipt['url'] = run['html_url']
    run_receipt['jobs'] = [{'id': j['id'], 'name': j['name'], 'status': j['status'], 'conclusion': j['conclusion'], 'steps': j['steps']} for j in jobs['jobs']]
    write(OUT / f'{args.stage}-run-{args.run}.json', run_receipt)
    receipt[args.stage + '_run'] = run_receipt
    if args.stage == 'gate':
        assert run['head_sha'] == status['release_candidate_sha']
        status['gate_run'] = run_receipt
    if args.stage == 'main_ci':
        status['main_ci_run'] = run_receipt
status['route_harness_repair'] = receipt
status['updated_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
write(MAIN / 'release-status.json', status)
write(OUT / 'release-status.json', receipt)
print(json.dumps({'stage': args.stage, 'pr': pr['state'], 'candidate': status['release_candidate_sha'], 'run': args.run, 'conclusion': receipt.get(args.stage + '_run', {}).get('conclusion'), 'runtime_files': len(runtime['runtime_files'])}))
