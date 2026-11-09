"""Derive a figure from retained synthetic tasks, preserving full source text."""
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
BASE = ROOT / 'artifacts/agent-dev-notes/history-intent-release-20261108'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

snapshot = BASE / 'audit-for-figure.json'
if not snapshot.exists():
    snapshot.write_bytes((BASE / 'audit.json').read_bytes())
audit = read(snapshot)
before = read(BASE / 'live-extra/result.json')
after = read(BASE / 'live-c2-extra/result.json')
old = next(t for t in before['tasks'] if t['case_id'] == 'X03')['turns'][1]
new = next(t for t in after['tasks'] if t['case_id'] == 'X03')['turns'][1]
assert old['message'] == new['message']
assert '2026-11-08（已完成）' in old['response']['reply']
assert '2026-11-07（已完成）' in new['response']['reply']
paths = [snapshot, BASE / 'live-extra/result.json', BASE / 'live-c2-extra/result.json',
         BASE / 'live-extra/reviews.json', BASE / 'live-c2-extra/reviews.json',
         BASE / 'checks-c2/backend-tests.xml']
data = {
    'kind': 'metrics_illustration_not_product_screenshot',
    'data_source': 'local_synthetic_tasks_real_model',
    'question_verbatim': old['message'],
    'business_date': '2026-11-09',
    'before_reply_verbatim': old['response']['reply'],
    'after_reply_verbatim': new['response']['reply'],
    'quote_locator': 'live-extra / live-c2-extra result.json: tasks[case_id=X03].turns[1]',
    'candidates': [
        {'commit': '137461d', 'core_completed': 16, 'core_total': 16, 'known_regression_completed': 2, 'known_regression_total': 3, 'recovery_completed': 1, 'wording_completed': 3, 'wording_role': 'first_unseen_output_not_blind'},
        {'commit': 'f4d388f', 'core_completed': 16, 'core_total': 16, 'known_regression_completed': 3, 'known_regression_total': 3, 'recovery_completed': 1, 'wording_completed': 3, 'wording_role': 'retest'},
    ],
    'total_http': audit['total_provider_calls'],
    'estimated_cny': audit['total_estimated_cny_including_failed_candidate'],
    'backend_tests': int(audit['backend_tests']['tests']),
    'human_feedback_n': 0,
    'limitations': ['Not an independent blind evaluation.', 'No phone metrics.', 'Correct date on the live second candidate was first-attempt; mechanism proven separately by recorded-error replay.', 'Additional sets/reps/volume remain; strict minimal columns not separately scored.'],
    'sources': [{'path': str(p.relative_to(ROOT)).replace('\\', '/'), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
}
(HERE / 'source.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print('Figure source derived; complete before/after responses retained. No model calls.')
