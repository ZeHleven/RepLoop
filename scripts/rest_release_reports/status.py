"""Read-only progress from append-only provider logs and saved task results."""
from pathlib import Path
from collections import Counter
import json

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/rest-release-acceptance-20261111'
phases = []
for phase in ['preparation', 'formal']:
    folder = OUT / f'live-{phase}-01'
    events = folder / 'provider-events.jsonl'
    if not events.exists():
        continue
    calls = {}
    for line in events.read_text(encoding='utf-8').splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # Only the current partial append can be incomplete.
        calls[row['id']] = row
    values = list(calls.values())
    known = sum(c.get('cost_nano') or 0 for c in values if c.get('usage_complete'))
    reserved = sum(c['reserved_nano'] for c in values if not c.get('usage_complete'))
    result_path = folder / 'result.json'
    result = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {}
    item = {'phase': phase, 'http_started': len(values), 'http_finished': sum(bool(c.get('complete')) for c in values),
        'known_nano': known, 'unsettled_reservations_nano': reserved,
        'last_task': values[-1]['task_id'] if values else None,
        'statuses': dict(Counter(str(c.get('status_code', 'in_flight')) for c in values)),
        'stopped': result.get('stopped'), 'exited': (folder / 'exit.json').exists()}
    if phase == 'preparation':
        item['tasks'] = [{'id': t['id'], 'complete': t['collection_complete'], 'turns': len(t['turns'])}
                         for t in result.get('tasks', [])]
    phases.append(item)
print(json.dumps({'phases': phases, 'total_http': sum(x['http_started'] for x in phases),
    'total_known_cny': sum(x['known_nano'] for x in phases) / 1e9,
    'total_reserved_cny': sum(x['unsettled_reservations_nano'] for x in phases) / 1e9}, ensure_ascii=False))
