"""Persist release receipts without credentials or browser automation."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
SOURCE = ROOT / '.tmp/agent-history-intent-20261108'
parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['pr', 'ci', 'main_ci', 'merge', 'gate'])
parser.add_argument('--run', type=int)
args = parser.parse_args()
env = os.environ.copy()
env.update(HTTP_PROXY='http://127.0.0.1:7897', HTTPS_PROXY='http://127.0.0.1:7897')

def gh(*params):
    process = subprocess.run(['gh', *params], env=env, check=True, capture_output=True, text=True, encoding='utf-8')
    return json.loads(process.stdout)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

status_path = OUT / 'release-status.json'
status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {
    'repo': 'ZeHleven/RepLoop',
    'branch': 'codex/agent-history-intent-20261108',
    'measured_commit': 'f4d388f078048e786ee97221b18ba6b4ba42892b',
    'local_tests': 2082, 'local_real_tasks_completed': 23, 'local_real_tasks_total': 23,
    'local_http_total_all_candidates': 184, 'local_estimated_cny_all_candidates': 1.227114,
    'deployment': {'status': 'not_started', 'verified_online_sha': None},
    'phone': {'status': 'not_started', 'samples': 0},
    'computer_use': {'status': 'stopped_by_tool', 'reason': 'Could not determine current browser URL on Windows with enough confidence to enforce policy; no retry or UI workaround.'},
    'github_connector_write': {'create_pr_error': '403 Resource not accessible by integration', 'fallback': 'User-authenticated gh via process-scoped local proxy'},
}
if args.stage in {'pr', 'ci', 'merge'}:
    pr = gh('pr', 'view', '46', '--repo', 'ZeHleven/RepLoop', '--json', 'number,url,state,headRefOid,baseRefOid,mergeStateStatus,mergedAt,mergeCommit,statusCheckRollup')
    write('pr-' + args.stage + '.json', pr)
    status['pr'] = pr
    if args.stage == 'merge':
        assert pr['state'] == 'MERGED' and pr['mergeCommit']['oid']
        status['release_candidate_sha'] = pr['mergeCommit']['oid']
if args.run:
    run = gh('api', 'repos/ZeHleven/RepLoop/actions/runs/' + str(args.run))
    jobs = gh('api', 'repos/ZeHleven/RepLoop/actions/runs/' + str(args.run) + '/jobs')
    receipt = {'id': run['id'], 'url': run['html_url'], 'event': run['event'], 'head_sha': run['head_sha'], 'name': run['name'], 'status': run['status'], 'conclusion': run['conclusion'], 'run_attempt': run['run_attempt'], 'created_at': run['created_at'], 'updated_at': run['updated_at'],
               'jobs': [{'id': j['id'], 'name': j['name'], 'status': j['status'], 'conclusion': j['conclusion'], 'steps': j['steps']} for j in jobs['jobs']]}
    write(f'{args.stage}-run-{args.run}.json', receipt)
    status[args.stage + '_run'] = receipt
candidate = json.loads((OUT / 'candidate.json').read_text(encoding='utf-8'))
assert all(sha(SOURCE / p) == digest for p, digest in candidate['source_files'].items())
status['backend_files_equal_measured_candidate'] = len(candidate['source_files'])
status['updated_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
write('release-status.json', status)
print(json.dumps({'stage': args.stage, 'pr': status.get('pr', {}).get('state'), 'candidate_sha': status.get('release_candidate_sha'), 'run': args.run, 'run_status': status.get(args.stage + '_run', {}).get('status'), 'run_conclusion': status.get(args.stage + '_run', {}).get('conclusion'), 'backend_files_equal': len(candidate['source_files'])}))
