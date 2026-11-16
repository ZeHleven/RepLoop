"""Keep a reviewable immutable patch and link the unchanged deployed release."""
from pathlib import Path
import hashlib
import json
import subprocess

ROOT=Path(__file__).resolve().parents[2]
SOURCE=ROOT/'.tmp/agent-next-workout-prep-20261110'
OUT=ROOT/'artifacts/agent-dev-notes/next-workout-prep-20261110'
def load(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=SOURCE,text=True).strip()
assert head=='415fe17387b8844f20622deaf194f90e02bcf67b'
assert not subprocess.check_output(['git','status','--porcelain'],cwd=SOURCE).strip()
tested=load(OUT/'ci2/source-manifest.json')
assert all(hashlib.sha256((SOURCE/p).read_bytes()).hexdigest()==h for p,h in tested.items())
patch=subprocess.check_output(['git','show','--format=fuller','--binary','HEAD'],cwd=SOURCE)
target=OUT/'candidate-415fe17.patch'
assert not target.exists();target.write_bytes(patch)
validation=load(OUT/'validation.json')
validation.update(candidate_commit=head,clean_source=True,
    candidate_patch=target.relative_to(ROOT).as_posix(),candidate_patch_sha256=hashlib.sha256(patch).hexdigest(),
    status='draft_pr_48_created_ci_pending_live_and_deployment_pending',
    github=load(OUT/'github-candidate.json')['pr'],test_database_status='stopped_data_retained')
write(OUT/'validation.json',validation)
release=ROOT/'artifacts/agent-dev-notes/history-intent-release-20261108/release-status.json'
state=load(release)
state['deployment']['status']='phone_advice_failure_observed_local_fix_ready'
state['phone']['fix_candidate']={'commit':head,'pr':'https://github.com/ZeHleven/RepLoop/pull/48',
    'status':'draft_local_tests_passed','deployed':False,'evidence':'artifacts/agent-dev-notes/next-workout-prep-20261110/validation.json'}
write(release,state)
note=ROOT/'docs/agent/dev-notes/next-workout-prep-20261110.md'
with note.open('a',encoding='utf-8') as stream:
    stream.write('\n\n发布准备：候选已固定为 `'+head+'`，实测源码指纹保持、候选检出干净。草稿 [PR #48](https://github.com/ZeHleven/RepLoop/pull/48) 已创建，基线仍为已部署7d2315c；GitHub CI运行中。测试库已停止并保留。推送首次因沙箱/用户账户所有权不同被Git拒绝，仅对本次进程信任已核验的具体目录后成功，没有改全局Git信任。未合并、未部署、未调用真实模型。\n')
print(json.dumps({'commit':head,'clean':True,'patch_sha256':validation['candidate_patch_sha256'],'draft_pr':48}))
