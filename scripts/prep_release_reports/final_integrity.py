"""Seal stopped acceptance evidence without new model calls or deployment."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/preparation-release-acceptance-20261110'
SOURCE = ROOT / '.tmp/agent-prep-advice-20261110'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


v = read(OUT / 'validation.json')
assert v['candidate'] == subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
freeze = read(OUT / 'preflight-freeze.json')
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == h for p, h in freeze['source_files'].items())
assert all(hashlib.sha256((ROOT / 'scripts/prep_release_acceptance' / p).read_bytes()).hexdigest() == h for p, h in freeze['harness_files'].items())
guard = ET.parse(OUT / 'offline-guard.xml').getroot()
assert len(guard.findall('.//testcase')) == 10 and not guard.findall('.//failure') and not guard.findall('.//error')
assert v['budget']['http'] == 226 and v['budget']['known_estimated_nano'] == 2798736000
assert v['budget']['provider_errors'] == 0 and v['budget']['usage_complete'] == 226
assert v['budget']['unknown_provider_occupancy_nano'] == 0
stop = read(OUT / 'budget-stop-explanation.json')
assert stop['remaining_nano'] == 3000000000 - v['budget']['known_estimated_nano'] == 201264000
assert stop['next_request_reservation_nano'] == 236712000 > stop['remaining_nano']
assert v['stopped'] == 'request_or_cost_budget' and not v['overall_acceptance_passed']
assert v['preparation']['quality_outcomes'] == {'partial': 1, 'completed': 1}
assert v['route']['passed_count'] == v['route']['total'] == 33 and v['route']['critical_passed'] == 9
assert v['meal_passes'][0]['original_aggregate_passed'] is True
assert v['meal_passes'][1]['artifact_successes_observed'] == 29
assert v['meal_passes'][1]['incomplete_or_failed_ids'] == ['meal-p2:30']
assert v['meal_passes'][1]['unrun_cases'] == [31]
assert v['meal_passes'][1]['original_aggregate_passed'] is None
formal = read(OUT / 'live-formal-01/sql-postcheck.json')
prep = read(OUT / 'live-preparation-01/sql-postcheck.json')
assert formal['pending_runs'] == prep['pending_runs'] == 0
assert formal['meal_rows'] == formal['proposal_rows'] == prep['proposal_rows'] == 0
assert prep['meal_rows'] == 4 and prep['plans'] == 2 and prep['sessions'] == 6
assert formal['budgets']['reserved_nano'] == 236712000 and formal['budgets']['blocked_users'] == 1
assert prep['budgets']['spent_nano'] + formal['budgets']['spent_nano'] == 2798736000
assert read(OUT / 'test-resources.json')['state'] == 'exited'
pr = read(OUT / 'github-pr-final.json')
assert pr['head_sha'] == v['candidate'] and pr['draft'] and not pr['merged']
assert pr['body'].strip().replace('\r\n', '\n') == (OUT / 'pr-body.txt').read_text(encoding='utf-8').strip()
release = read(ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json')
assert release['release_candidate_sha'] == '7d2315ca6f3ee61b0ab7204d1c37d697abafc372'
assert release['phone']['samples'] == 5 and release['phone']['latency_samples'] == 0
assert release['phone']['fix_candidate']['evidence'].endswith('preparation-release-acceptance-20261110/validation.json')
package = read(ROOT / 'artifacts/agent-dev-notes/preparation-advice-quality-20261110/package-verification.json')
assert hashlib.sha256((ROOT / package['package']).read_bytes()).hexdigest() == package['sha256']
v['test_resources'] = read(OUT / 'test-resources.json')
v['budget_stop_evidence'] = 'budget-stop-explanation.json'
v['pr'] = {'url': pr['url'], 'head_sha': pr['head_sha'], 'draft': True, 'merged': False}
v['materials'] = {'draft_only': True, 'published': False, 'figure': 'docs/agent/dev-notes/assets/preparation-release-acceptance-20261110/acceptance-results-v2.png', 'figure_render_reviewed': True, 'type': 'synthetic result diagram, not phone screenshot'}
(OUT / 'validation.json').write_text(json.dumps(v, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
paths = set()
for folder in [OUT, ROOT / 'scripts/prep_release_acceptance', ROOT / 'scripts/prep_release_reports',
               ROOT / 'docs/agent/dev-notes/assets/preparation-release-acceptance-20261110']:
    paths.update(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name != 'final-integrity.json')
for name in ['preparation-release-acceptance-20261110.md', 'preparation-release-acceptance-20261110-wechat-draft.md',
             'deploy-preparation-advice-20261110.md', 'issues.md', 'README.md']:
    paths.add(ROOT / 'docs/agent/dev-notes' / name)
paths.add(ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json')
key = dotenv_values(ROOT / '.env').get('DEEPSEEK_API_KEY')
hashes = {}
for p in sorted(paths):
    data = p.read_bytes()
    assert not key or key.encode() not in data, 'Model key present in retained evidence'
    hashes[p.relative_to(ROOT).as_posix()] = hashlib.sha256(data).hexdigest()
result = {'created_at': datetime.now(timezone.utc).isoformat(), 'candidate': v['candidate'],
          'candidate_clean': True, 'source_files_verified': len(freeze['source_files']),
          'harness_files_verified': len(freeze['harness_files']), 'paid_http': 226,
          'known_estimated_cny': 2.798736, 'provider_unknown_usage_occupancy_cny': 0,
          'unsent_local_ledger_reservation_cny': 0.236712, 'within_authorized_limits': True,
          'overall_acceptance_passed': False, 'github_protected_workflow_run': False,
          'merged': False, 'deployed': False, 'phone_samples_added': 0,
          'test_container_stopped_data_retained': True, 'model_key_absent_from_files': len(hashes),
          'files': hashes}
(OUT / 'final-integrity.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'files'}))
