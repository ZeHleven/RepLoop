"""Check immutable evidence, source identities and secrets without provider calls."""
from pathlib import Path
import hashlib
import json
import subprocess
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/next-workout-prep-20261110'
SOURCE = ROOT / '.tmp/agent-next-workout-prep-20261110'
BASE = ROOT / '.tmp/agent-release-route-billing-20261108'


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def git(*args, cwd=SOURCE):
    return subprocess.check_output(['git', *args], cwd=cwd, text=True).strip()


report = load(OUT / 'validation.json')
assert git('rev-parse', 'HEAD') == report['candidate_commit'] == '929b84dfd91df0daa51cc346ec7913e0bbe23539'
assert not git('status', '--porcelain')
assert git('rev-parse', 'HEAD', cwd=BASE) == '7d2315ca6f3ee61b0ab7204d1c37d697abafc372'
assert not git('status', '--porcelain', cwd=BASE)
tested = load(OUT / 'ci3/source-manifest.json')
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == h for p, h in tested.items())
pr = load(OUT / 'github-candidate-929b84d.json')['pr']
assert pr['head_sha'] == report['candidate_commit'] and pr['draft'] and not pr['merged']
assert report['provider_http_calls'] == 10 and report['conservative_total_budget_occupancy_cny'] == 0.169114
assert report['deployed'] is False and report['full_task_live_gate_passed'] is False
key = dotenv_values(ROOT / '.env').get('DEEPSEEK_API_KEY')
files = {}
paths = set()
for folder in [OUT, ROOT / 'scripts/next_workout_prep', ROOT / 'docs/agent/dev-notes/assets/next-workout-prep-20261110']:
    paths.update(p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.name != 'final-integrity.json')
paths.update(ROOT / p for p in [
    'docs/agent/dev-notes/next-workout-prep-20261110.md',
    'docs/agent/dev-notes/next-workout-prep-20261110-wechat-draft.md',
    'docs/agent/dev-notes/issues.md', 'docs/agent/dev-notes/README.md',
    'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json'])
for path in sorted(paths):
    content = path.read_bytes()
    assert not key or key.encode() not in content, 'Model key found in retained artifact'
    files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(content).hexdigest()
result = {'candidate': report['candidate_commit'], 'candidate_clean': True,
          'deployed_release_checkout_clean': True, 'tested_source_files_match': len(tested),
          'provider_http_attempts': 10, 'known_estimated_cny': 0.051146,
          'unknown_cost_occupancy_cny': 0.117968, 'conservative_total_occupancy_cny': 0.169114,
          'full_task_live_gate_passed': False, 'deployed': False, 'phone_retest_performed': False,
          'model_key_absent_from_scanned_files': len(files), 'files': files}
(OUT / 'final-integrity.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: v for k, v in result.items() if k != 'files'}))
