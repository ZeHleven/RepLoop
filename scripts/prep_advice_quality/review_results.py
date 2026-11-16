"""Seal a human review of the frozen, single-attempt P03/P04 batch (no models)."""
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/preparation-advice-quality-20261110'
SOURCE = ROOT / '.tmp/agent-prep-advice-20261110'
SHA = '5816ac5f498d7f6073c42c514968c7046f35f80c'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(name, value):
    path = OUT / name
    if path.exists():
        assert read(path) == value, f'Existing evidence changed: {name}'
        return
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=SOURCE)


assert git('rev-parse', 'HEAD').decode().strip() == SHA
assert not git('status', '--porcelain').strip()
prereg = read(OUT / 'live-preregistration.json')
assert prereg['candidate'] == SHA
for name, digest in prereg['files'].items():
    assert hashlib.sha256((ROOT / 'scripts/prep_advice_quality/observer' / name).read_bytes()).hexdigest() == digest
tested = read(OUT / 'ci/source-manifest.json')
assert all(hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == digest for name, digest in tested.items())
raw = read(OUT / 'live-01/result.json')
ledger = read(OUT / 'live-01/ledger.json')
assert raw['source_commit'] == SHA and raw['stopped'] is None
assert [task['id'] for task in raw['tasks']] == ['serial-P03', 'serial-P04']
assert [len(task['turns']) for task in raw['tasks']] == [5, 1]
assert len(raw['provider_calls']) == len(ledger['charges']) == 19
assert ledger['pending_runs'] == 0
assert all(c['complete'] and c['usage_complete'] and c['status_code'] == 200 and c['error_type'] is None for c in raw['provider_calls'])
assert all(c['status'] == 'settled' for c in ledger['charges'])
assert Counter(c['cost_nano'] for c in raw['provider_calls']) == Counter(c['actual_nano'] for c in ledger['charges'])
assert sum(c['cost_nano'] for c in raw['provider_calls']) == 129844000
for task in raw['tasks']:
    assert task['before'] == task['after']
    assert all(t['business_after'] == task['before'] for t in task['turns'])
    for t in task['turns']:
        r = t['response']
        assert r['status'] == 'completed' and r['error_code'] is None
        assert r['requested_effect'] == 'read' and not r['change_requests']
        assert r['execution_trace']['evidence_contract']['status'] == 'complete'
        assert all(a['status'] == 'completed' and a['tool'] in {'plan.get_active', 'workout.get_next', 'workout.list_history'} for a in t['tool_audits'])
business_date = date.fromisoformat(raw['business_date'])
p03, p04 = raw['tasks']
expected = [(business_date - timedelta(days=7), business_date, 2),
            (business_date - timedelta(days=1), business_date, 1),
            (business_date - timedelta(days=2), business_date - timedelta(days=2), 1)]
for turn, (start, end, count) in zip(p03['turns'][1:4], expected):
    audit = turn['tool_audits'][0]
    assert audit['tool'] == 'workout.list_history'
    assert audit['arguments']['start_date'] == start.isoformat()
    assert audit['arguments']['end_date'] == end.isoformat()
    assert audit['arguments']['completed_only'] is True and audit['result']['count'] == count
assert any(a['tool'] == 'plan.get_active' and a['call_id'].startswith('required-') for a in p03['turns'][4]['tool_audits'])
assert p04['turns'][0]['tool_audits'][0]['result']['found'] is False
assert all(not p[2] for p in p04['after']['plan_states'])

# These explanations are the implementer's manual review of the retained full replies,
# not an independent/blind model judge and not a phrase-matching quality score.
reviews = {
    'serial-P03': {
        'outcome': 'completed',
        'reviewer': 'implementer_manual_not_blind',
        'evidence': [
            'live-01/result.json: tasks[0].turns[0:5], replies, tool_audits, execution_trace and before/after',
            'The three history windows are 11-04..11-11, 11-10..11-11 and 11-09 only; completed dates and 30/12 minute durations match seeded SQL, excluding today in_progress.',
            'Final answer uses next Monday 2026-11-16, bench/squat 3x10 at plan targets 20/30 kg; required plan.get_active was actually recovered after workout.get_next.',
            'Final reply explicitly does not infer personal intensity from kg; says to end the set when form/control fails and reduce weight/reps after rest, without forcing 10 reps.',
            'Zero completed days is expressly scoped to the current plan, consistent with unrelated completed history. All tracked business snapshots unchanged; no proposal or write tool.'
        ],
        'nonblocking_findings': ['History replies include unsolicited set/rep/volume summaries although user asks only dates/minutes; output concision remains open.', 'Final preparation reply took 11.287520 s locally and used five provider HTTP requests; latency optimization remains open.'],
        'limits': ['One known regression sequence; no production/phone or independent review, no stability claim.']
    },
    'serial-P04': {
        'outcome': 'completed',
        'reviewer': 'implementer_manual_not_blind',
        'evidence': [
            'live-01/result.json: tasks[1].turns[0], reply, found=false, before/after',
            'Explicitly reports no active training plan and cannot identify next scheduled workout. Advice is labeled generic; no invented next date, exercise list, sets, or load.',
            'Inactive synthetic plan stays inactive, tracked business records unchanged, no proposals or write tools.'
        ],
        'limits': ['One isolated synthetic inactive-plan account, not every missing-data or tool-failure condition.']
    }
}
save('live-01/reviews.json', reviews)
spec = importlib.util.spec_from_file_location('frozen_metrics', ROOT / 'scripts/prep_advice_quality/observer/metrics.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)
save('live-01/metrics-reviewed.json', metrics.summarize(raw['tasks'], raw['provider_calls'], reviews))
save('live-01/reconciliation.json', {
    'provider_http_requests': 19, 'settled_charge_rows': 19, 'usage_complete': 19,
    'cost_multiset_matches': True, 'known_estimated_nano': 129844000,
    'pending_runs': 0, 'tracked_business_snapshots_unchanged': True,
    'total_authorized_http': 30, 'cumulative_http': 29,
    'cumulative_known_estimated_nano': 180990000,
    'prior_unknown_cost_occupancy_nano': 117968000,
    'cumulative_conservative_occupancy_nano': 298958000,
    'total_authorized_nano': 500000000,
    'cost_basis': 'application conservative cache-miss tariff, not provider invoice',
    'phone_samples_added': 0,
    'turn_timings': [{'task': t['id'], 'message': v['message'], 'visible_ms': v['visible_timing']['ms'], 'provider_calls': sum(c['run_id'] == v['run_id'] for c in raw['provider_calls'])} for t in raw['tasks'] for v in t['turns']]
})

package = SOURCE / 'deploy/cloudbase/fitness-agent-backend-0.5.47.zip'
entry_hashes = {}
crlf_normalized = []
with zipfile.ZipFile(package) as archive:
    assert archive.testzip() is None
    names = [i.filename.removeprefix('./') for i in archive.infolist() if not i.is_dir()]
    assert len(names) == len(set(names))
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        name = entry.filename.removeprefix('./')
        assert not Path(name).is_absolute() and '..' not in Path(name).parts
        assert not any(part.startswith('.env') or part in {'.git', 'tests', '__pycache__', 'node_modules'} for part in Path(name).parts)
        data = archive.read(entry)
        if name == 'app/build_metadata.json':
            metadata = json.loads(data)
            assert metadata['build_commit'] == SHA and metadata['source_dirty'] is False and metadata['build_version'] == '0.5.47'
        else:
            disk = (SOURCE / 'backend' / name).read_bytes()
            blob = git('show', f'{SHA}:backend/{name}')
            assert data == disk, f'Package differs from tested checkout: {name}'
            if data != blob:
                assert data.replace(b'\r\n', b'\n') == blob.replace(b'\r\n', b'\n'), f'Package has non-line-ending Git difference: {name}'
                crlf_normalized.append(name)
        entry_hashes[name] = hashlib.sha256(data).hexdigest()
    expected = {p[8:] for p in tested if p.startswith('backend/app/') and not p.endswith('build_metadata.json')}
    assert expected <= set(names), 'Missing tested application files'
destination = OUT / 'fitness-agent-backend-0.5.47-5816ac5.zip'
assert not destination.exists()
shutil.copyfile(package, destination)
save('package-verification.json', {
    'package': destination.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
    'bytes': destination.stat().st_size, 'build_metadata': metadata, 'file_count': len(entry_hashes),
    'entries_match_tested_checkout_bytes': True,
    'entries_match_frozen_git_after_crlf_normalization': True,
    'crlf_normalized_files': crlf_normalized, 'tested_application_files_included': len(expected),
    'entry_sha256': entry_hashes, 'deployed': False
})
patch = OUT / 'candidate-5816ac5.patch'
assert not patch.exists()
patch.write_bytes(git('diff', '--binary', '7d2315ca6f3ee61b0ab7204d1c37d697abafc372', SHA))
print(json.dumps({'reviewed_tasks': 2, 'completed_by_preregistered_checks': 2, 'http_total': 29, 'conservative_cny': 0.298958, 'package': str(destination), 'package_files': len(entry_hashes)}))
