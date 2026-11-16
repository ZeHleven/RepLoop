"""Preserve the measured baseline and construct a replay fixture before edits."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'artifacts/agent-dev-notes/current-experience-metrics-20261108/live-02'
OUT = ROOT / 'artifacts/agent-dev-notes/query-history-fix-20261108'
SOURCE = ROOT / '.tmp/agent-query-fix-20261108'
OUT.mkdir(parents=True, exist_ok=True)
snapshot = OUT / 'before-source'
snapshot.mkdir(exist_ok=True)
files = ('agent_task_state.py', 'workout_history_reporting.py', 'agent_query_reports.py', 'agent_intent_model.py', 'agent_tools.py')
manifest = {}
for name in files:
    path = SOURCE / 'backend/app/services' / name
    target = snapshot / name
    assert not target.exists(), 'baseline evidence already exists'
    shutil.copyfile(path, target)
    manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
raw = json.loads((BASE / 'result.json').read_text(encoding='utf-8'))
fixture = {'source_commit': raw['source_commit'], 'source': str(BASE.relative_to(ROOT)).replace('\\', '/') + '/result.json', 'cases': []}
for task in raw['tasks']:
    if task['case_id'] != 'Q03':
        continue
    first, second = task['turns']
    reply = first['response']
    routes = []
    for call in raw['provider_calls']:
        if call['run_id'] != second['run_id']:
            continue
        for tool in call['response']['choices'][0]['message'].get('tool_calls', []):
            if tool['function']['name'] == 'submit_semantic_route':
                routes.append({'http_id': call['id'], 'route': json.loads(tool['function']['arguments'])})
    data = next(c['data'] for c in reply['cards'] if c['type'] == 'workout.list_history')
    context = [
        {'role': 'task_state', 'content': json.dumps(reply['execution_trace']['task_state'], ensure_ascii=False)},
        {'role': 'user', 'content': first['message']},
        {'role': 'assistant', 'content': reply['reply'], 'query_context': {
            'source_run_id': first['run_id'], 'successful_query': True,
            **{key: reply[key] for key in ('primary_intent', 'request_kind', 'requested_effect', 'risk_level', 'resolved_query')},
        }},
    ]
    fixture['cases'].append({'task_id': task['id'], 'first_message': first['message'], 'first_tool_result': data,
        'first_reply': reply['reply'], 'follow_message': second['message'], 'context': context, 'provider_routes': routes})
target = SOURCE / 'backend/tests/fixtures/query_history_20261108.json'
assert not target.exists()
target.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf-8')
manifest['fixture_sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
manifest['baseline_result_sha256'] = hashlib.sha256((BASE / 'result.json').read_bytes()).hexdigest()
(OUT / 'before-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
note = ROOT / 'docs/agent/dev-notes/query-history-fix-20261108.md'
shutil.copyfile(note, SOURCE / 'docs/agent/dev-notes' / note.name)
issues = SOURCE / 'docs/agent/dev-notes/issues.md'
text = issues.read_text(encoding='utf-8')
issues.write_text(text.replace('# Agent 问题台账', '# Agent 问题台账\n\n**2026-11-08｜QUERY-20261108-01｜修改前登记。** 修复真实模型基线中历史时长漏交付和只读日期修订误拦；冻结原响应、源码与验收，按[本轮计划](query-history-fix-20261108.md)执行。', 1), encoding='utf-8')
print(json.dumps({'source_files_saved': len(files), 'original_cases': len(fixture['cases']), 'provider_routes': sum(len(c['provider_routes']) for c in fixture['cases'])}))
