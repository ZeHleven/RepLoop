"""Seal offline evidence and a clean candidate; never claim live-model acceptance."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-inference-boundaries-20261115'
OUT = ROOT / 'artifacts/agent-inference-boundaries-20261115'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def git(*args):
    return subprocess.check_output(['git', *args], cwd=SOURCE).decode('utf-8').strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_counts(label, filename='tests.xml'):
    report = ET.parse(OUT / label / filename).getroot()
    return {'total': len(report.findall('.//testcase')),
            'failed': len(report.findall('.//failure')),
            'errors': len(report.findall('.//error')),
            'skipped': len(report.findall('.//skipped'))}


baseline = read(OUT / 'preflight.json')
head = git('rev-parse', 'HEAD')
assert head != baseline['baseline_commit']
assert not git('status', '--porcelain')
changed = git('diff', '--name-only', baseline['baseline_commit'], head).splitlines()
assert set(changed) == {
    'backend/app/services/agent_training_advice.py',
    'backend/app/services/agent_tools.py',
    'backend/tests/test_preparation_advice_contract.py',
    'backend/tests/test_preparation_rest_scope.py',
    'backend/tests/test_training_advice_inference_boundaries.py',
}
assert digest(OUT / 'original-findings.json') == baseline['original_findings_sha256']
assert digest(ROOT / baseline['original_findings']) == baseline['original_findings_sha256']
for label in ['before', 'before-corrected']:
    snapshot = read(OUT / label / 'source-manifest.json')
    assert all(snapshot[name] == value for name, value in baseline['backend_files'].items()
               if name.startswith('backend/app/'))
    assert read(OUT / label / 'result.json')['exit_code'] == 1
assert test_counts('before-corrected') == {'total': 10, 'failed': 9, 'errors': 0, 'skipped': 0}
assert test_counts('targeted') == {'total': 64, 'failed': 0, 'errors': 0, 'skipped': 0}
full = test_counts('ci', 'backend-tests.xml')
assert full == {'total': 2128, 'failed': 0, 'errors': 0, 'skipped': 0}
checks = read(OUT / 'ci/checks.json')
assert len(checks) == 8 and all(item['exit_code'] == 0 for item in checks)
assert read(OUT / 'ci/result.json')['exit_code'] == 0
ci_source = read(OUT / 'ci/source-manifest.json')
assert all(digest(SOURCE / path) == value for path, value in ci_source.items())
assert 'Public candidate scan passed' in (OUT / 'public-source-check.log').read_text(encoding='utf-8-sig')

package = OUT / f'fitness-agent-backend-0.5.47-{head[:7]}.zip'
with zipfile.ZipFile(package) as archive:
    assert archive.testzip() is None
    files = [item for item in archive.infolist() if not item.is_dir()]
    metadata = json.loads(archive.read('./app/build_metadata.json'))
    assert metadata['build_commit'] == head and metadata['source_dirty'] is False
    assert metadata['build_version'] == '0.5.47'
    for item in files:
        name = item.filename.removeprefix('./')
        assert '..' not in Path(name).parts and not name.startswith('/')
        assert not any(p.startswith('.env') or p in {'.git', 'tests', '__pycache__'}
                       for p in Path(name).parts)
        if name == 'app/build_metadata.json':
            continue
        # Packaged bytes must be exactly the tested checkout, including its EOLs.
        assert archive.read(item) == (SOURCE / 'backend' / name).read_bytes(), name

pr = read(OUT / 'github-pr-final.json')
assert pr['headRefOid'] == head and pr['isDraft'] and pr['state'] == 'OPEN'
ci_checks = pr['statusCheckRollup']
assert ci_checks and all(item['conclusion'] == 'SUCCESS' for item in ci_checks)
assert {item['name'] for item in ci_checks} >= {
    'Backend tests and Agent evals', 'Mini program typecheck and build',
}
resources = read(OUT / 'test-resources.json')
assert resources['state'] == 'exited' and resources['data_retained']
assert not resources['host_ports']
pending = read(OUT / 'pending-live-cases.json')
assert pending['status'] == 'planned_not_executed' and len(pending['cases']) == 8

result = {
    'created_at': datetime.now(timezone.utc).isoformat(),
    'baseline': baseline['baseline_commit'], 'candidate': head, 'candidate_clean': True,
    'changed_files': changed, 'verified_ci_source_files': len(ci_source),
    'before_corrected': test_counts('before-corrected'), 'targeted': test_counts('targeted'),
    'backend_full': full, 'offline_ci_checks': checks,
    'setup_failures': baseline['setup_attempts'] + [{
        'action': 'initial SQL counterexample date fixture',
        'outcome': 'Agent date was fixed but training calendar was not; four fixture failures retained in before/. Both calendars fixed in tests only, then before-corrected/ confirms missing inference metadata on unchanged app code.',
    }],
    'public_source_check_passed': True,
    'package': {'path': package.relative_to(ROOT).as_posix(), 'bytes': package.stat().st_size,
                'sha256': digest(package), 'files': len(files), 'metadata': metadata},
    'pr': {'url': pr['url'], 'head_sha': head, 'draft': True, 'ci_checks': ci_checks},
    'test_resources': resources,
    'model_http_calls': 0, 'new_model_cost_cny': 0,
    'live_answer_quality_verified': False, 'pending_live_cases': len(pending['cases']),
    'phone_retest_performed': False, 'merged': False, 'deployed': False,
    'limitations': 'Offline checks verify SQL scope, no mutation, facts and guidance delivered to the three actual answer paths. Scripted model replies are not generated-answer quality evidence. Previous live scores belong only to 2f8eca1.',
}
result['evidence_sha256'] = {p.relative_to(ROOT).as_posix(): digest(p)
    for folder in [OUT, ROOT / 'scripts/inference_boundaries'] for p in sorted(folder.rglob('*'))
    if p.is_file() and p.name != 'verification.json' and '__pycache__' not in p.parts}
(OUT / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({key: value for key, value in result.items()
                  if key not in {'evidence_sha256', 'pr', 'offline_ci_checks'}}, ensure_ascii=False))
