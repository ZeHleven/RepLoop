"""Preserve existing synthetic failures and immutable pre-change source."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / '.tmp/agent-history-intent-20261108'
OUT = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'
OUT.mkdir(exist_ok=False)
head = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
assert head == '3d37905f96d6947f1019949e0fdae5246aa54e50'
assert not subprocess.check_output(['git', '-C', str(SOURCE), 'status', '--porcelain']).strip()
paths = subprocess.check_output(['git', '-C', str(SOURCE), 'ls-files', 'backend'], text=True).splitlines()
manifest = {'base': head, 'source_files': {p: hashlib.sha256((SOURCE/p).read_bytes()).hexdigest() for p in paths}}
(OUT/'before-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
for rel in ['backend/app/services/agent_intent.py','backend/app/services/agent_intent_model.py','backend/app/services/agent_task_state.py','backend/app/schemas/agent_task.py']:
 target=OUT/'before-source'/rel
 target.parent.mkdir(parents=True,exist_ok=True)
 shutil.copy2(SOURCE/rel,target)
old=ROOT/'artifacts/agent-dev-notes/query-history-fix-20261108/live-c2-extra/result.json'
raw=json.loads(old.read_text(encoding='utf-8')); task=next(t for t in raw['tasks'] if t['id']=='serial-X01')
calls=[c for c in raw['provider_calls'] if c['run_id'] in {t['run_id'] for t in task['turns']}]
evidence={'source':str(old.relative_to(ROOT)).replace('\\','/'),'source_sha256':hashlib.sha256(old.read_bytes()).hexdigest(),'task':task,'provider_calls':calls}
(OUT/'original-x01.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
fixture={'source':evidence['source'],'source_sha256':evidence['source_sha256'],'first_message':task['turns'][0]['message'],'first_routes':[], 'follow_message':task['turns'][1]['message'],'follow_routes':[], 'failed_context':[{'role':'user','content':task['turns'][0]['message']},{'role':'assistant','content':task['turns'][0]['response']['reply']}]}
for call in calls:
 for tc in call['response']['choices'][0]['message'].get('tool_calls',[]):
  if tc['function']['name']=='submit_semantic_route':
   key='first_routes' if call['run_id']==task['turns'][0]['run_id'] else 'follow_routes'
   fixture[key].append(json.loads(tc['function']['arguments']))
assert len(fixture['first_routes'])==len(fixture['follow_routes'])==2
target=SOURCE/'backend/tests/fixtures/history_intent_20261108.json'
target.write_text(json.dumps(fixture,ensure_ascii=False,indent=2),encoding='utf-8')
note=Path('docs/agent/dev-notes/history-intent-release-20261108.md')
shutil.copy2(ROOT/note,SOURCE/note);shutil.copy2(ROOT/note,OUT/'PROTOCOL.md')
issues=SOURCE/'docs/agent/dev-notes/issues.md'
entry=(ROOT/'docs/agent/dev-notes/issues.md').read_text(encoding='utf-8').split('\n\n')[1]
body=issues.read_text(encoding='utf-8');head_line,rest=body.split('\n\n',1)
issues.write_text(head_line+'\n\n'+entry+'\n\n'+rest,encoding='utf-8')
print(json.dumps({'base':head,'source_files':len(paths),'recorded_routes':4,'new_model_calls':0}))
