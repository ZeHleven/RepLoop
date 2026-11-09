"""Rebuild the figure's traceable data from retained, synthetic task evidence."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
BASE = ROOT / 'artifacts/agent-dev-notes/query-history-fix-20261108'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

audit_snapshot = BASE / 'audit-for-figure.json'
if not audit_snapshot.exists():
    audit_snapshot.write_bytes((BASE / 'audit.json').read_bytes())
audit = read(audit_snapshot)
raw = read(BASE / 'live-c2-core/result.json')
task = next(t for t in raw['tasks'] if t['id'] == 'serial-Q03')
reply = task['turns'][1]['response']['reply']
paths = [
    ROOT / 'artifacts/agent-dev-notes/current-experience-metrics-20261108/live-02/result.json',
    audit_snapshot,
    *[BASE / name / 'result.json' for name in ('live-core', 'live-c2-core', 'live-c2-extra')],
    BASE / 'extra-replay-baseline.json',
    BASE / 'checks-c2/backend-tests.xml',
]
paths.append(paths[0].parent.parent / 'reviews.json')
baseline_reviews = read(paths[-1])
assert sum(r['outcome'] == 'completed' for r in baseline_reviews.values()) == 14
data = {
    'kind': 'metrics_illustration_not_product_screenshot',
    'data_source': 'local_synthetic_tasks_real_model',
    'fixed_tasks': [
        {'label': '原主线', 'commit': '51feceb', 'completed': 14, 'total': 16, 'partial': 2},
        {'label': '第一候选', 'commit': 'da1b95c', 'completed': audit['batches']['live-core']['outcomes']['completed'], 'total': 16, 'partial': 1},
        {'label': '最终候选', 'commit': 'b440578', 'completed': audit['batches']['live-c2-core']['outcomes']['completed'], 'total': 16, 'partial': 0},
    ],
    'extra': {'completed': audit['batches']['live-c2-extra']['outcomes']['completed'], 'total': 3, 'partial': 1},
    'total_http': audit['total_provider_calls'],
    'estimated_cny': audit['total_estimated_cny_including_failed_candidate'],
    'backend_tests': int(audit['backend_tests']['tests']),
    'question_verbatim': task['turns'][1]['message'],
    'reply_verbatim': reply,
    'reply_rows_verbatim': [line for line in reply.splitlines() if line.startswith('- 2026')],
    'quote_locator': 'live-c2-core/result.json: tasks[id=serial-Q03].turns[1]',
    'limitations': ['Same implementer authored and reviewed the tasks; not a blind evaluation.', 'No phone or live-user feedback.', 'Additional sets/reps/volume remain in the report; minimal-column formatting was not scored separately.'],
    'sources': [{'path': str(p.relative_to(ROOT)).replace('\\', '/'), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
}
(HERE / 'source.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Figure data rebuilt from retained evidence; no model calls.')
