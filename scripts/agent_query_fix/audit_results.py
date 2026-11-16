"""Recompute all-candidate accounting and evidence checks without model calls."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from dotenv import dotenv_values
from observer.metrics import summarize

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'artifacts/agent-dev-notes/query-history-fix-20261108'
SOURCE = ROOT / '.tmp/agent-query-fix-20261108'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    reports, total_cost, total_calls = {}, 0, 0
    for name, count in [('live-core', 16), ('live-c2-core', 16), ('live-c2-extra', 3)]:
        folder = BASE / name
        raw, ledger, freeze = (read(folder / f) for f in ('result.json', 'ledger.json', 'freeze.json'))
        reviews = read(folder / 'reviews.json')
        assert not raw['stopped'] and len(raw['tasks']) == count
        assert all(t['collection_complete'] for t in raw['tasks'])
        calls = raw['provider_calls']
        runs = {v['run_id']: t['id'] for t in raw['tasks'] for v in t['turns']}
        assert len({c['id'] for c in calls}) == len(calls)
        assert all(runs[c['run_id']] == c['task_id'] and c['complete'] and c['usage_complete'] and c['status_code'] == 200 for c in calls)
        expected, actual = defaultdict(int), defaultdict(int)
        for c in calls:
            expected[c['task_id']] += c['cost_nano']
        for c in ledger['charges']:
            assert c['status'] == 'settled'
            actual[c['task_id']] += c['actual_nano']
        assert expected == actual and len(calls) == len(ledger['charges']) and ledger['pending_runs'] == 0
        for t in raw['tasks']:
            for turn in t['turns']:
                assert all(turn['business_after'][d] == t['before'][d] for d in ('plan', 'meals'))
            if t['case_id'] not in ('W01', 'W02'):
                assert t['before'] == t['after']
            else:
                assert t['confirmation']['status'] == 200 and t['confirmation']['response']['applied']
        verified = read(folder / 'source-verification.json')
        assert verified['before_after_equal']
        persistence = read(folder / 'persistence-verification.json')
        assert persistence['pending_runs'] == 0 and len(persistence['tasks']) == count
        summary = summarize(raw['tasks'], calls, reviews, [])
        (folder / 'metrics.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        total_cost += sum(expected.values())
        total_calls += len(calls)
        reports[name] = {'commit': raw['source_commit'], 'tasks': count, 'runs': len(runs),
            'outcomes': dict(Counter(r['outcome'] for r in reviews.values())), 'provider_calls': len(calls),
            'cost_cny_estimate': sum(expected.values()) / 1e9, 'ledger_matches': True, 'pending_runs': 0,
            'source_files_unchanged': verified['files'], 'metrics': summary}
    assert total_calls <= 160 and total_cost <= 5_000_000_000
    final = read(BASE / 'candidate.json')
    assert all(sha(SOURCE / file) == digest for file, digest in final['source_files'].items())
    checks = read(BASE / 'checks-c2/checks.json')
    assert len(checks) == 8 and all(item['exit_code'] == 0 for item in checks)
    suite = ET.parse(BASE / 'checks-c2/backend-tests.xml').getroot()[0].attrib
    assert suite['failures'] == suite['errors'] == '0'
    key = dotenv_values(ROOT / '.env').get('DEEPSEEK_API_KEY')
    scanned = 0
    for path in BASE.rglob('*'):
        if path.is_file() and path.suffix in {'.json', '.jsonl', '.log', '.md', '.py', '.patch'}:
            assert not key or key not in path.read_text(encoding='utf-8'), 'secret_in_artifact'
            scanned += 1
    result = {'batches': reports, 'total_provider_calls': total_calls,
        'total_estimated_cny_including_failed_candidate': total_cost / 1e9, 'provider_invoice_cny': None,
        'limits': {'http_attempts': 160, 'estimated_cny': 5}, 'backend_tests': suite,
        'human_feedback_n': 0, 'phone_render_latency': None, 'known_key_absent_from_files': scanned,
        'final_backend_source_unchanged': len(final['source_files'])}
    (BASE / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'batches': {k: {p: v[p] for p in ('tasks', 'runs', 'outcomes', 'provider_calls', 'cost_cny_estimate')} for k, v in reports.items()},
        'total_provider_calls': total_calls, 'total_estimated_cny': total_cost / 1e9, 'backend_tests': suite['tests']}))


if __name__ == '__main__':
    main()
