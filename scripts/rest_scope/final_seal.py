"""Finalize immutable candidate evidence and the current handoff, offline."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/preparation-rest-scope-20261111'
SOURCE = ROOT / '.tmp/agent-rest-scope-20261111'
SHA = '2f8eca12370aa8c6ed0992d709789333b2fa0ccb'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


assert subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD']).decode().strip() == SHA
assert not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain']).strip()
manifest = read(OUT / 'ci/source-manifest.json')
for name, expected in manifest.items():
    assert hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() == expected, name
for name, expected in read(OUT / 'preserved-origins.json').items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
pr = read(OUT / 'github-pr.json')
assert pr['head_sha'] == SHA and pr['draft'] and not pr['merged']
assert pr['body'].strip().replace('\r\n', '\n') == (OUT / 'pr-body.txt').read_text(encoding='utf-8').strip()
ci = read(OUT / 'github-ci.json')
assert ci['commit'] == SHA and ci['run']['head_sha'] == SHA and ci['run']['conclusion'] == 'success'
assert all(r['conclusion'] == 'success' for r in ci['all_commit_runs_snapshot']['workflow_runs'])
assert len(ci['jobs']['jobs']) == 2 and all(j['conclusion'] == 'success' for j in ci['jobs']['jobs'])
resources = read(OUT / 'test-resources.json')
assert resources['container']['state'] == 'exited' and resources['container']['data_retained']
package = read(OUT / 'package-verification.json')
assert hashlib.sha256((ROOT / package['path']).read_bytes()).hexdigest() == package['sha256']
release = read(ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json')
assert release['release_candidate_sha'] == '7d2315ca6f3ee61b0ab7204d1c37d697abafc372'
assert release['phone']['fix_candidate']['commit'] == SHA and not release['phone']['fix_candidate']['deployed']
assert release['phone']['samples'] == 5 and release['phone']['latency_samples'] == 0
validation = read(OUT / 'validation.json')
validation.update({'remote_ci': {'pr_run': ci['run']['html_url'], 'all_commit_runs_success': True,
    'jobs_successful': 2}, 'pr': {'url': pr['url'], 'head_sha': SHA, 'draft': True, 'merged': False},
    'test_resources': resources, 'tested_files_verified': len(manifest),
    'followup_plan': 'next-acceptance-plan.json', 'followup_authorized': False})
save(OUT / 'validation.json', validation)
paths = [p for p in OUT.rglob('*') if p.is_file() and p.name != 'final-integrity.json']
paths += [p for p in (ROOT / 'scripts/rest_scope').rglob('*') if p.is_file() and '__pycache__' not in p.parts]
paths += [ROOT / relative for relative in [
    'docs/agent/dev-notes/preparation-rest-scope-20261111.md',
    'docs/agent/dev-notes/preparation-rest-scope-20261111-wechat-draft.md',
    'docs/agent/dev-notes/assets/preparation-rest-scope-20261111/rest-scope-contract.png',
    'docs/agent/dev-notes/deploy-preparation-rest-scope-20261111.md',
    'docs/agent/dev-notes/issues.md', 'docs/agent/dev-notes/README.md',
    'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json']]
sealed = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(paths))}
save(OUT / 'final-integrity.json', {'checked_at': datetime.now(timezone.utc).isoformat(),
    'commit': SHA, 'source_clean': True, 'tested_files': len(manifest),
    'model_calls': 0, 'deployed': False, 'sealed_files': sealed})
print(json.dumps({'commit': SHA, 'tested_files': len(manifest), 'sealed_files': len(sealed),
    'backend_passed': validation['tests']['full_backend']['passed'], 'ci': 'success',
    'draft': True, 'deployed': False, 'model_http': 0}))
