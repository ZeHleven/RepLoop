"""Hash retained round evidence and verify deployment provenance without models."""
import hashlib
import json
from pathlib import Path
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
MAIN = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
HARNESS = ROOT / 'artifacts/agent-dev-notes/release-route-billing-20261108'
status = json.loads((MAIN / 'release-status.json').read_text(encoding='utf-8'))
source = ROOT / status.get('package_source_path', '.tmp/agent-history-intent-20261108')
key = dotenv_values(ROOT / '.env').get('DEEPSEEK_API_KEY')
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=source).strip()
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
assert head == status['release_candidate_sha']
files = {}
scanned = 0
folders = [MAIN, HARNESS, ROOT / 'scripts/history_intent_release',
           ROOT / 'docs/agent/dev-notes/assets/history-intent-release-20261108']
client_evidence = status.get('client_refresh', {}).get('evidence_directory')
if client_evidence:
    client_folder = (ROOT / client_evidence).resolve()
    assert client_folder.is_relative_to((ROOT / 'artifacts').resolve())
    folders.append(client_folder)
for folder in folders:
    for path in sorted(folder.rglob('*')):
        if not path.is_file() or '__pycache__' in path.parts or path.name == 'final-integrity.json':
            continue
        content = path.read_bytes()
        if key:
            assert key.encode() not in content, 'Model key found in retained artifact'
        scanned += 1
        files[str(path.relative_to(ROOT)).replace('\\', '/')] = hashlib.sha256(content).hexdigest()
for path in [ROOT / 'docs/agent/reviews/2026-11-08-history-intent-release.md',
             *([ROOT / 'docs/miniapp-sync-071-20261109.md'] if client_evidence else []),
             *[ROOT / 'docs/agent/dev-notes' / name for name in [
                 'history-intent-release-20261108.md', 'history-intent-release-20261108-wechat-draft.md',
                 'history-intent-release-20261108-phone-checklist.md', 'deploy-history-intent-release-20261108.md',
                 'release-route-billing-20261108.md', 'release-route-billing-20261108-wechat-draft.md']]]:
    files[str(path.relative_to(ROOT)).replace('\\', '/')] = hashlib.sha256(path.read_bytes()).hexdigest()
if status.get('package'):
    package = ROOT / status['package']['package']
    assert hashlib.sha256(package.read_bytes()).hexdigest() == status['package']['sha256']
    assert status['formal_gate_verified']['passed']
    assert status['package']['build_metadata']['build_commit'] == status['formal_gate_verified']['candidate_sha'] == head
    files[str(package.relative_to(ROOT)).replace('\\', '/')] = status['package']['sha256']
result = {'head': head, 'clean_source': True, 'local_model_key_absent_from_scanned_files': scanned,
          'deployment_status': status['deployment']['status'], 'phone_samples': status['phone']['samples'],
          'formal_gate_passed': status.get('formal_gate_verified', {}).get('passed'), 'files': files}
(MAIN / 'final-integrity.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'files'}))
