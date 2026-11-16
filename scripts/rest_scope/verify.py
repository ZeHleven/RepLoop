"""Seal offline results and a local candidate package; never call a model."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-rest-scope-20261111'
OUT = ROOT / 'artifacts/agent-dev-notes/preparation-rest-scope-20261111'
SHA = '2f8eca12370aa8c6ed0992d709789333b2fa0ccb'
BASELINE = '5816ac5f498d7f6073c42c514968c7046f35f80c'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def git(*args, source=SOURCE):
    return subprocess.check_output(['git', '-C', str(source), *args])


def counts(path):
    root = ET.parse(path).getroot()
    cases = root.findall('.//testcase')
    failures = len(root.findall('.//failure'))
    errors = len(root.findall('.//error'))
    skipped = len(root.findall('.//skipped'))
    return {'passed': len(cases) - failures - errors - skipped,
            'failed': failures, 'errors': errors, 'skipped': skipped}


assert git('rev-parse', 'HEAD').decode().strip() == SHA
assert not git('status', '--porcelain').strip()
old = ROOT / '.tmp/agent-prep-advice-20261110'
assert git('rev-parse', 'HEAD', source=old).decode().strip() == BASELINE
assert not git('status', '--porcelain', source=old).strip()
for name, expected in read(OUT / 'preserved-origins.json').items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
for phase in ['after', 'targeted', 'ci']:
    assert read(OUT / phase / 'result.json')['exit_code'] == 0
    for name, expected in read(OUT / phase / 'source-manifest.json').items():
        assert hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == expected, name
for path in ['backend/app/services/agent_tools.py', 'backend/app/services/agent_training_advice.py']:
    # The fresh Windows checkout has CRLF while the prior patch retained LF.
    # Reconstructed baseline bytes must match the actual before-run manifest;
    # normalization is allowed only when comparing to the immutable Git blob.
    baseline_bytes = (OUT / 'before-tested-code' / path).read_bytes()
    assert hashlib.sha256(baseline_bytes).hexdigest() == read(OUT / 'before-corrected/source-manifest.json')[path]
    assert baseline_bytes.replace(b'\r\n', b'\n') == git('show', f'{BASELINE}:{path}').replace(b'\r\n', b'\n')
tests = {'before_with_fixture_error': counts(OUT / 'before/tests.xml'),
         'before_corrected': counts(OUT / 'before-corrected/tests.xml'),
         'after': counts(OUT / 'after/tests.xml'),
         'targeted': counts(OUT / 'targeted/tests.xml'),
         'full_backend': counts(OUT / 'ci/backend-tests.xml')}
assert tests['before_corrected'] == {'passed': 1, 'failed': 5, 'errors': 0, 'skipped': 0}
assert tests['after']['passed'] == 6 and tests['targeted']['passed'] == 60
assert tests['full_backend'] == {'passed': 2124, 'failed': 0, 'errors': 0, 'skipped': 0}
checks = read(OUT / 'ci/checks.json')
assert len(checks) == 8 and all(item['exit_code'] == 0 for item in checks)
package = SOURCE / 'deploy/cloudbase/fitness-agent-backend-0.5.47.zip'
entries = {}
normalized = []
with zipfile.ZipFile(package) as archive:
    assert archive.testzip() is None
    for entry in archive.infolist():
        if entry.is_dir():
            continue
        name = entry.filename.removeprefix('./')
        assert name not in entries and not Path(name).is_absolute() and '..' not in Path(name).parts
        assert not any(p.startswith('.env') or p in {'.git', 'tests', '__pycache__'} for p in Path(name).parts)
        content = archive.read(entry)
        if name == 'app/build_metadata.json':
            metadata = json.loads(content)
            assert metadata['build_commit'] == SHA and metadata['build_version'] == '0.5.47'
            assert metadata['source_dirty'] is False
        else:
            assert content == (SOURCE / 'backend' / name).read_bytes(), name
            blob = git('show', f'{SHA}:backend/{name}')
            assert content.replace(b'\r\n', b'\n') == blob.replace(b'\r\n', b'\n'), name
            if content != blob:
                normalized.append(name)
        entries[name] = hashlib.sha256(content).hexdigest()
    tested_app = {p.removeprefix('backend/') for p in read(OUT / 'ci/source-manifest.json') if p.startswith('backend/app/')}
    assert tested_app <= set(entries)
destination = OUT / f'fitness-agent-backend-0.5.47-{SHA[:7]}.zip'
assert not destination.exists()
shutil.copyfile(package, destination)
package_check = {'path': destination.relative_to(ROOT).as_posix(),
    'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(), 'bytes': destination.stat().st_size,
    'file_count': len(entries), 'build_metadata': metadata, 'entry_sha256': entries,
    'crlf_normalized_files': normalized, 'entries_match_tested_checkout_bytes': True,
    'tested_application_files_included': len(tested_app), 'deployed': False}
save('package-verification.json', package_check)
(OUT / 'change-from-5816.patch').write_bytes(git('diff', '--binary', BASELINE, SHA))
(OUT / 'candidate-from-deployed-7d2315.patch').write_bytes(git('diff', '--binary', '7d2315ca6f3ee61b0ab7204d1c37d697abafc372', SHA))
validation = {'issue': 'ADVICE-20261110-02', 'commit': SHA, 'baseline_commit': BASELINE,
    'status': 'offline_fix_verified_live_acceptance_pending', 'source_clean': True,
    'source_path': SOURCE.relative_to(ROOT).as_posix(), 'tests': tests, 'offline_checks': checks,
    'model_http_calls': 0, 'estimated_cny': 0, 'live_model_improvement_verified': False,
    'phone_retest': False, 'merged': False, 'deployed': False,
    'previous_authorization_closed': {'http': 226, 'estimated_cny': 2.798736,
        'reason': 'next reservation exceeded remaining cost limit', 'source': 'preparation-release-acceptance-20261110'},
    'package': {k: v for k, v in package_check.items() if k not in {'entry_sha256', 'crlf_normalized_files'}},
    'limitations': ['Checks prove tool fact and guidance delivery, not real model wording stability.',
        'Current SHA real task/routing/two meal-pass acceptance remains unrun.',
        'Phone case 5 retest, case 6 and timings remain outstanding.']}
save('validation.json', validation)
print(json.dumps({k: validation[k] for k in ['commit', 'tests', 'model_http_calls']}, ensure_ascii=False))
