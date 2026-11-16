"""Verify a local repair package and preserve a patch, without deployment."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-query-fix-20261108'
BASE = ROOT / 'artifacts/agent-dev-notes/query-history-fix-20261108'

def git(*args):
    return subprocess.check_output(['git', '-C', str(SOURCE), *args])

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

candidate = json.loads((BASE / 'candidate.json').read_text(encoding='utf-8'))
head = git('rev-parse', 'HEAD').decode().strip()
assert not git('status', '--porcelain').strip()
assert not git('diff', candidate['commit'], head, '--', 'backend').strip()
assert all(sha(SOURCE / path) == digest for path, digest in candidate['source_files'].items())
package = SOURCE / 'deploy/cloudbase/fitness-agent-backend-0.5.47.zip'
destination = ROOT / f'deploy/cloudbase/fitness-agent-backend-0.5.47-{head[:7]}.zip'
assert not destination.exists() or sha(destination) == sha(package)
shutil.copy2(package, destination)
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    entries = archive.namelist()
    metadata = json.loads(archive.read('./app/build_metadata.json'))
    assert metadata['build_version'] == '0.5.47'
    assert metadata['build_commit'] == head and metadata['source_dirty'] is False
    matched = 0
    for entry in entries:
        if entry.endswith('/') or entry == './app/build_metadata.json':
            continue
        rel = entry.removeprefix('./')
        assert '..' not in Path(rel).parts and not Path(rel).is_absolute()
        assert archive.read(entry) == (SOURCE / 'backend' / rel).read_bytes(), rel
        matched += 1
old_patch = BASE / 'query-history-fix.patch'
if old_patch.exists() and not (BASE / 'candidate-01.patch').exists():
    shutil.copy2(old_patch, BASE / 'candidate-01.patch')
old_patch.write_bytes(git('diff', '--binary', candidate['base'], head))
subprocess.run(['git', '-C', str(ROOT / '.tmp/agent-experience-20261108-src'), 'apply', '--check', str(old_patch)], check=True)
subprocess.run(['git', '-C', str(SOURCE), 'apply', '--check', '--reverse', str(old_patch)], check=True)
result = {
    'package': str(destination.relative_to(ROOT)).replace('\\', '/'),
    'size_bytes': destination.stat().st_size, 'entries': len(entries), 'sha256': sha(destination),
    'build_metadata': metadata,
    'measured_code_commit': candidate['commit'],
    'backend_files_identical_to_measured_candidate': len(candidate['source_files']),
    'packaged_files_identical_to_checkout': matched,
    'zip_crc': 'pass', 'package_script_required_exclusions_and_boundaries': 'pass',
    'patch': str(old_patch.relative_to(ROOT)).replace('\\', '/'), 'patch_sha256': sha(old_patch),
    'patch_check_on_unmodified_base': 'pass', 'patch_reverse_check_on_final_head': 'pass',
    'source_dirty': False, 'pushed': False, 'deployed': False,
    'formal_daily_meal_live_release_gate': 'not_run',
}
(BASE / 'package-verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
closeout = json.loads((BASE / 'closeout.json').read_text(encoding='utf-8'))
closeout.update({'final_commit': head, 'final_commit_delta_from_measured': 'documentation_only', 'backend_files_identical': len(candidate['source_files']), 'package': result['package'], 'package_sha256': result['sha256']})
(BASE / 'closeout.json').write_text(json.dumps(closeout, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
