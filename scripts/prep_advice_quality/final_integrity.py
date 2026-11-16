"""Finalize local evidence and handoff; no network or model calls."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/preparation-advice-quality-20261110'
SOURCE = ROOT / '.tmp/agent-prep-advice-20261110'
SHA = '5816ac5f498d7f6073c42c514968c7046f35f80c'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def git(*args, source=SOURCE):
    return subprocess.check_output(['git', *args], cwd=source).decode().strip()


assert git('rev-parse', 'HEAD') == SHA and not git('status', '--porcelain')
for name, sha in [('.tmp/agent-next-workout-prep-20261110', '929b84dfd91df0daa51cc346ec7913e0bbe23539'),
                  ('.tmp/agent-release-route-billing-20261108', '7d2315ca6f3ee61b0ab7204d1c37d697abafc372')]:
    assert git('rev-parse', 'HEAD', source=ROOT / name) == sha
    assert not git('status', '--porcelain', source=ROOT / name)
tested = read(OUT / 'ci/source-manifest.json')
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == h for p, h in tested.items())
suite = ET.parse(OUT / 'ci/backend-tests.xml').getroot()
tests = suite.findall('.//testcase')
assert len(tests) == 2118 and not suite.findall('.//failure') and not suite.findall('.//error')
checks = read(OUT / 'ci/checks.json')
assert len(checks) == 8 and all(row['exit_code'] == 0 for row in checks)
remote = read(OUT / 'github-ci-5816ac5.json')
assert remote['commit'] == SHA
assert all(x['status'] == 'completed' and x['conclusion'] == 'success' for x in remote['runs']['workflow_runs'])
assert len(remote['jobs']['jobs']) == 2 and all(x['conclusion'] == 'success' for x in remote['jobs']['jobs'])
pr = read(OUT / 'github-pr-5816ac5.json')
assert pr['head_sha'] == SHA and pr['draft'] and not pr['merged']
assert pr['body'].strip().replace('\r\n', '\n') == (OUT / 'pr-body.txt').read_text(encoding='utf-8').strip()
recon = read(OUT / 'live-01/reconciliation.json')
assert recon['cumulative_http'] == 29 and recon['cumulative_conservative_occupancy_nano'] == 298958000
sql = read(OUT / 'live-01/sql-postcheck.json')
assert sql['pending_runs'] == 0 and sql['proposals'] == 0
assert sql['run_statuses'] == [{'status': 'completed', 'n': 6}]
assert sql['charges'] == [{'status': 'settled', 'n': 19, 'actual_nano': 129844000}]
assert sql['budgets'] == {'spent_nano': 129844000, 'reserved_nano': 0, 'blocked_users': 0}
reviewed = read(OUT / 'live-01/metrics-reviewed.json')['groups'][0]
assert reviewed['benchmark_complete'] and reviewed['outcomes'] == {'completed': 2}
resources = read(OUT / 'test-resources.json')
assert all(c['state'] == 'exited' and c['data_retained'] for c in resources['containers'])
pkg = read(OUT / 'package-verification.json')
package = ROOT / pkg['package']
assert hashlib.sha256(package.read_bytes()).hexdigest() == pkg['sha256']
assert pkg['build_metadata']['build_commit'] == SHA and not pkg['build_metadata']['source_dirty']
assert pkg['entries_match_tested_checkout_bytes'] and pkg['file_count'] == 209
status = read(ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json')
assert status['release_candidate_sha'] == '7d2315ca6f3ee61b0ab7204d1c37d697abafc372'
assert status['phone']['fix_candidate']['commit'] == SHA
assert status['phone']['samples'] == 5 and status['phone']['latency_samples'] == 0

validation = {
    'issue': 'ADVICE-20261110-01', 'candidate_commit': SHA,
    'status': 'local_fix_and_preregistered_P03_P04_complete_not_deployed',
    'source_path': SOURCE.relative_to(ROOT).as_posix(), 'source_clean': True,
    'tests': {'before': {'passed': 1, 'failed': 5, 'fixture_errors': 0},
              'targeted': {'passed': 102, 'warnings': 1},
              'full_backend': {'passed': 2118, 'warnings': 869},
              'offline_checks': checks},
    'remote_ci': {'run': 42972470074, 'conclusion': 'success', 'jobs_successful': 2},
    'pr': {'url': pr['url'], 'head_sha': SHA, 'draft': True, 'merged': False},
    'real_batch': {'tasks': 2, 'turns': 6, 'completed_by_preregistered_criteria': 2,
                   'cases': ['P03', 'P04'], 'attempts_per_case': 1,
                   'reviewer': 'implementer_manual_not_blind', 'http': 19,
                   'provider_errors': 0, 'known_estimated_cny': 0.129844,
                   'business_date': '2026-11-11', 'environment': 'local_tcp_single_worker',
                   'data': 'synthetic', 'pending_runs': 0, 'proposals': 0,
                   'tracked_business_snapshots_unchanged': True},
    'budget_all_attempts': {'http': 29, 'authorized_http': 30, 'known_estimated_cny': 0.180990,
                            'unknown_occupancy_cny': 0.117968, 'conservative_occupancy_cny': 0.298958,
                            'authorized_cny': 0.50, 'provider_invoice': False},
    'candidate_package': {k: v for k, v in pkg.items() if k not in {'entry_sha256', 'crlf_normalized_files'}},
    'historical_results_retained': {'415fe17': '1 partial / 1 failed / 2 unrun',
                                    '929b84d': 'one successful answer-only replay, not complete API task'},
    'current_sha_full_P01_P02_live_retested': False,
    'current_sha_formal_paid_release_gates_run': False,
    'deployed': False, 'phone_retest_performed': False,
    'phone': {'samples': 5, 'user_reported_correct': 4, 'screenshot_failure': 1, 'latency_samples': 0},
    'open_items': ['Current-SHA full short/long tasks and formal paid routing/nutrition release workflows',
                   'Deployment and phone case 5 retest / case 6 diet-history query and timings',
                   'Preparation request local latency 11.288 s; history reply excess detail',
                   'No stability or production success-rate claim; original phone tool trace unavailable'],
    'public_materials': {'draft_only': True, 'figure': 'synthetic mechanism diagram, not phone screenshot',
                        'render_verified': True},
    'test_resources': resources,
}
dump(OUT / 'validation.json', validation)
paths = set()
for folder in [OUT, ROOT / 'scripts/prep_advice_quality', ROOT / 'docs/agent/dev-notes/assets/preparation-advice-quality-20261110']:
    paths.update(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name != 'final-integrity.json')
for name in ['preparation-advice-quality-20261110.md', 'preparation-advice-quality-20261110-wechat-draft.md',
             'deploy-preparation-advice-20261110.md', 'issues.md', 'README.md']:
    paths.add(ROOT / 'docs/agent/dev-notes' / name)
paths.add(ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json')
key = dotenv_values(ROOT / '.env').get('DEEPSEEK_API_KEY')
file_hashes = {}
for p in sorted(paths):
    data = p.read_bytes()
    assert not key or key.encode() not in data, 'Model key in retained material'
    file_hashes[p.relative_to(ROOT).as_posix()] = hashlib.sha256(data).hexdigest()
with zipfile.ZipFile(package) as archive:
    for entry in archive.infolist():
        if not entry.is_dir():
            assert not key or key.encode() not in archive.read(entry), 'Model key in package'
final = {'created_at': datetime.now(timezone.utc).isoformat(), 'candidate': SHA,
         'candidate_and_prior_checkouts_clean': True, 'tested_source_files_verified': len(tested),
         'ci_success': True, 'pr_draft_not_merged': True, 'http_total': 29,
         'conservative_occupancy_cny': 0.298958, 'current_sha_formal_paid_release_gates_run': False,
         'deployed': False, 'phone_retest_performed': False, 'retained_files_without_model_key': len(file_hashes),
         'files': file_hashes}
dump(OUT / 'final-integrity.json', final)
print(json.dumps({k: v for k, v in final.items() if k != 'files'}))
