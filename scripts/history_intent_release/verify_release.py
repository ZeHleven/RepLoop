"""Verify exact-SHA formal evidence, then make a source-checked release ZIP."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
SOURCE = ROOT / '.tmp/agent-history-intent-20261108'
parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['gate', 'package'])
args = parser.parse_args()

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

status = read(OUT / 'release-status.json')
candidate = read(OUT / 'candidate.json')
sha = status['release_candidate_sha']
SOURCE = ROOT / status.get('package_source_path', '.tmp/agent-history-intent-20261108')
assert SOURCE.resolve().is_relative_to((ROOT / '.tmp').resolve())
if args.stage == 'gate':
    run = status['gate_run']
    folder = ROOT / status.get('formal_gate_artifacts_dir', 'artifacts/agent-dev-notes/history-intent-release-20261108/formal-gate')
    route = read(folder / 'agent-route-live-eval.json')
    meal = read(folder / 'daily-meal-live-eval.json')
    assert route['candidate_sha'] == meal['candidate_sha'] == run['head_sha'] == sha
    assert run['status'] == 'completed' and run['event'] == 'workflow_dispatch'
    passed = bool(run['conclusion'] == 'success' and route['passed'] and meal['passed'])
    if passed:
        assert route['total'] == 33 and route['critical_passed'] == route['critical_total']
        assert route['structural_valid_count'] == route['total'] and route['accuracy'] >= .95
        assert meal['repeat'] == 2 and len(meal['passes']) == 2
        for item in meal['passes']:
            assert item['candidate_sha'] == sha and item['passed']
            assert item['original_successes'] == item['original_runs'] == 10
            assert item['synonym_runs'] == 21 and item['synonym_success_rate'] >= .95
            assert all(item[key] == 0 for key in ['optimizer_unavailable_runs', 'unconfirmed_meal_writes', 'proposal_count', 'generation_as_mutation_count', 'rules_fallback_runs'])
    result = {
        'candidate_sha': sha, 'passed': passed, 'run_id': run['id'], 'url': run['url'],
        'route': {k: route.get(k) for k in ['total', 'passed_count', 'accuracy', 'critical_total', 'critical_passed', 'ordinary_rules_fallbacks', 'passed', 'fatal_error']},
        'meal_passes': [{k: item.get(k) for k in ['pass_index', 'original_successes', 'original_runs', 'synonym_successes', 'synonym_runs', 'optimizer_unavailable_runs', 'unconfirmed_meal_writes', 'proposal_count', 'rules_fallback_runs', 'passed']} for item in meal.get('passes', [])],
        'meal_passed': meal['passed'], 'meal_fatal_error': meal.get('fatal_error'),
        'route_billing': route.get('billing'),
        'provider_http_calls': None, 'estimated_cny': None, 'provider_invoice_cny': None,
        'cost_note': 'Existing formal workflow reports outcomes but no full HTTP usage or charges. Do not count these as zero or combine them with the local 184-call/1.227114 CNY measurement.',
        'source_reports': [{'path': str(p.relative_to(ROOT)).replace('\\', '/'), 'sha256': digest(p)} for p in sorted(folder.glob('*.json'))],
    }
    canonical = OUT / 'formal-gate-verification.json'
    if canonical.exists():
        old = read(canonical)
        historical = OUT / ('formal-gate-verification-' + old['candidate_sha'][:7] + '.json')
        if not historical.exists():
            historical.write_bytes(canonical.read_bytes())
    write('formal-gate-verification-' + sha[:7] + '.json', result)
    write('formal-gate-verification.json', result)
    status['formal_gate_verified'] = result
    write('release-status.json', status)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if passed else 1)

assert status['formal_gate_verified']['passed'], 'Do not prepare a publishable package before the formal gate passes.'
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip()
dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE, text=True)
assert head == sha and not dirty
changed = [p for p, value in candidate['source_files'].items() if digest(SOURCE / p) != value]
line_endings_only = []
if status.get('route_harness_repair'):
    # Fresh Windows checkout applies CRLF to these tests; they are excluded
    # from the package. Prove both normalized bytes and Git objects agree.
    for p in ['backend/tests/test_history_intent_release.py', 'backend/tests/test_history_revision_dates.py']:
        if p not in changed:
            continue
        old = ROOT / '.tmp/agent-history-intent-20261108' / p
        assert (SOURCE / p).read_bytes().replace(b'\r\n', b'\n') == old.read_bytes().replace(b'\r\n', b'\n')
        original_blob = subprocess.check_output(['git', 'rev-parse', status['measured_commit'] + ':' + p], cwd=SOURCE).strip()
        current_blob = subprocess.check_output(['git', 'rev-parse', 'HEAD:' + p], cwd=SOURCE).strip()
        assert current_blob == original_blob
        line_endings_only.append(p)
    assert [p for p in changed if p not in line_endings_only] == ['backend/scripts/evaluate_agent_routing_real.py'], changed
    runtime = read(ROOT / 'artifacts/agent-dev-notes/release-route-billing-20261108/source-verification.json')
    assert all(digest(SOURCE / p) == value for p, value in runtime['runtime_files'].items())
else:
    assert not changed
script = SOURCE / 'scripts/package_cloudbase_backend.ps1'
result = subprocess.run(['pwsh', '-NoProfile', '-File', str(script), '-Version', '0.5.47'], cwd=SOURCE, capture_output=True, text=True, encoding='utf-8', errors='replace')
(OUT / 'package-build.log').write_text(result.stdout + result.stderr, encoding='utf-8')
assert result.returncode == 0, 'Packaging failed; retained package-build.log.'
source_zip = SOURCE / 'deploy/cloudbase/fitness-agent-backend-0.5.47.zip'
target_zip = ROOT / f'deploy/cloudbase/fitness-agent-backend-0.5.47-{sha[:7]}.zip'
assert not target_zip.exists(), 'Preserve earlier packages; refuse overwrite.'
copied = 0
with zipfile.ZipFile(source_zip) as archive:
    assert archive.testzip() is None
    names = archive.namelist()
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        assert not path.is_absolute() and '..' not in path.parts
        assert not any(p in {'tests', '.git', '__pycache__', '.pytest_cache', '.venv'} or p.startswith('.env') for p in path.parts)
        if info.is_dir():
            continue
        name = str(path)
        content = archive.read(info)
        if name == 'app/build_metadata.json':
            metadata = json.loads(content)
            assert metadata['build_version'] == '0.5.47' and metadata['build_commit'] == sha and metadata['source_dirty'] is False
            continue
        assert content == (SOURCE / 'backend' / name).read_bytes(), f'Package differs from clean checkout: {name}'
        copied += 1
shutil.copyfile(source_zip, target_zip)
verification = {'package': str(target_zip.relative_to(ROOT)).replace('\\', '/'), 'size_bytes': target_zip.stat().st_size,
    'entries': len(names), 'sha256': digest(target_zip), 'build_metadata': metadata,
    'measured_code_commit': status['measured_commit'], 'backend_files_identical_to_measured_candidate': len(candidate['source_files']) - len(changed),
    'measured_backend_manifest_files': len(candidate['source_files']),
    'changed_evaluation_files_since_measurement': [p for p in changed if p not in line_endings_only],
    'same_git_blob_test_files_with_crlf_checkout': line_endings_only,
    'runtime_files_unchanged': status.get('route_harness_repair', {}).get('runtime_files_unchanged'),
    'packaged_files_identical_to_checkout': copied, 'zip_crc': 'pass', 'package_script_required_exclusions_and_boundaries': 'pass',
    'formal_gate_run': status['gate_run']['url'], 'formal_gate_candidate_sha': sha, 'source_dirty': False, 'deployed': False}
write('package-verification.json', verification)
status['package'] = verification
write('release-status.json', status)
print(json.dumps(verification, ensure_ascii=False))
