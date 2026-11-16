"""Seal the completed acceptance and remaining defects without paid calls."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/rest-release-acceptance-20261111'
SOURCE = ROOT / '.tmp/agent-rest-scope-20261111'
HARNESS = ROOT / 'scripts/rest_release_acceptance'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


v = read(OUT / 'validation.json')
assert v['candidate'] == subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
freeze = read(OUT / 'preflight-freeze.json')
for base, values in [(SOURCE, freeze['source_files']), (HARNESS, freeze['harness_files'])]:
    assert all(hashlib.sha256((base / p).read_bytes()).hexdigest() == h for p, h in values.items())
guard = ET.parse(OUT / 'offline-guard.xml').getroot()
assert len(guard.findall('.//testcase')) == 10 and not guard.findall('.//failure') and not guard.findall('.//error')
assert v['budget']['http'] == v['budget']['usage_complete'] == 253
assert v['budget']['known_estimated_nano'] == 3028904000 < v['budget']['authorized_nano'] == 4000000000
assert v['budget']['provider_errors'] == v['budget']['unknown_provider_occupancy_nano'] == 0
assert v['stopped'] is None and v['all_original_formal_checks_passed']
assert not v['overall_acceptance_passed'] and not v['deployed'] and not v['merged']
assert v['preparation']['quality_outcomes'] == {'completed': 3, 'partial': 2}
assert v['preparation']['registered_rest_scope_checks']['correct_interval_and_values'] == 4
assert v['preparation']['metrics']['invalid_visible_timings'] == 1
assert v['route']['passed_count'] == 33 and v['route']['critical_passed'] == 9
for p in v['meal_passes']:
    assert p['original_aggregate_passed'] and p['observed_runs'] == p['started_cases'] == 31 and not p['unrun_cases']
    r = p['original_report']
    assert r['original_successes'] == 10 and r['synonym_successes'] == 21
    assert r['fit_counts']['within_target'] == 31
for p in v['phase_reconciliation']:
    assert p['pending_runs'] == 0 and p['budgets']['reserved_nano'] == p['budgets']['blocked_users'] == 0
    assert not p['unsettled_charges'] and p['charge_statuses'] == {'settled': p['http']}
assert sum(p['budgets']['spent_nano'] for p in v['phase_reconciliation']) == v['budget']['known_estimated_nano']
resources = read(OUT / 'test-resources.json')
assert resources['state'] == 'exited' and resources['data_retained'] and not resources['host_ports']
pr = read(OUT / 'github-pr-final.json')
assert pr['head_sha'] == v['candidate'] and pr['draft'] and not pr['merged']
assert pr['body'].strip().replace('\r\n', '\n') == (OUT / 'pr-body.txt').read_text(encoding='utf-8').strip()
release = read(ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json')
assert release['release_candidate_sha'] == '7d2315ca6f3ee61b0ab7204d1c37d697abafc372'
assert release['phone']['samples'] == 5 and release['phone']['latency_samples'] == 0
assert release['phone']['fix_candidate']['evidence'].endswith('rest-release-acceptance-20261111/validation.json')
package = read(ROOT / 'artifacts/agent-dev-notes/preparation-rest-scope-20261111/package-verification.json')
assert hashlib.sha256((ROOT / package['path']).read_bytes()).hexdigest() == package['sha256']
assert package['build_metadata']['build_commit'] == v['candidate']
v['test_resources'] = resources
v['pr'] = {'url': pr['url'], 'head_sha': pr['head_sha'], 'draft': True, 'merged': False}
v['candidate_package'] = {k: package[k] for k in ['path', 'sha256', 'build_metadata']}
v['materials'] = {'draft_only': True, 'published': False,
    'figure': 'docs/agent/dev-notes/assets/rest-release-acceptance-20261111/acceptance-results.png',
    'figure_render_reviewed': True, 'type': 'synthetic result diagram, not phone screenshot'}
write(OUT / 'validation.json', v)
paths = set()
for folder in [OUT, HARNESS, ROOT / 'scripts/rest_release_reports',
               ROOT / 'docs/agent/dev-notes/assets/rest-release-acceptance-20261111']:
    paths.update(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name != 'final-integrity.json')
for name in ['rest-release-acceptance-20261111.md', 'rest-release-acceptance-20261111-wechat-draft.md',
             'deploy-preparation-rest-scope-20261111.md', 'issues.md', 'README.md']:
    paths.add(ROOT / 'docs/agent/dev-notes' / name)
paths.add(ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json')
values = dotenv_values(ROOT / '.env')
keys = [values.get(k) for k in ['DEEPSEEK_API_KEY', 'EMBEDDING_API_KEY'] if values.get(k)]
hashes = {}
for path in sorted(paths):
    content = path.read_bytes()
    assert all(k.encode() not in content for k in keys), 'Model key present in retained evidence'
    hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(content).hexdigest()
seal = {'created_at': datetime.now(timezone.utc).isoformat(), 'candidate': v['candidate'],
    'candidate_clean': True, 'source_files_verified': len(freeze['source_files']),
    'harness_files_verified': len(freeze['harness_files']), 'http': 253, 'known_estimated_cny': 3.028904,
    'unknown_provider_occupancy_cny': 0, 'within_authorized_limits': True,
    'all_original_formal_checks_passed': True, 'overall_acceptance_passed': False,
    'new_quality_defects': 2, 'github_protected_workflow_run': False,
    'merged': False, 'deployed': False, 'phone_samples_added': 0,
    'test_container_stopped_data_retained': True, 'model_keys_absent_from_files': len(hashes), 'files': hashes}
write(OUT / 'final-integrity.json', seal)
print(json.dumps({k: x for k, x in seal.items() if k != 'files'}))
