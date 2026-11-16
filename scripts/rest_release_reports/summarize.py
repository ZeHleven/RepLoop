"""Reconcile first-run observations, registered gates and separate new defects."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/rest-release-acceptance-20261111'
SOURCE = ROOT / '.tmp/agent-rest-scope-20261111'
HARNESS = ROOT / 'scripts/rest_release_acceptance'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line] if path.exists() else []


auth = read(OUT / 'authorization.json')
freeze = read(OUT / 'preflight-freeze.json')
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() == auth['candidate']
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
for base, values in [(SOURCE, freeze['source_files']), (HARNESS, freeze['harness_files'])]:
    assert all(hashlib.sha256((base / p).read_bytes()).hexdigest() == h for p, h in values.items())
prep = read(OUT / 'live-preparation-01/result.json')
formal = read(OUT / 'live-formal-01/result.json')
assert (OUT / 'live-formal-01/exit.json').exists(), 'Await request completion and cleanup'
assert prep['source_commit'] == formal['source_commit'] == auth['candidate']
assert prep['business_date'] == formal['business_date'] == freeze['business_date']
allowance = read(OUT / 'live-formal-01/phase-allowance.json')
prep_spent = sum(c['cost_nano'] for c in prep['provider_calls'])
assert allowance == {'prior_http': len(prep['provider_calls']), 'prior_nano': prep_spent,
    'remaining_http': auth['max_http_requests'] - len(prep['provider_calls']),
    'remaining_nano': auth['max_estimated_nano'] - prep_spent}
calls = prep['provider_calls'] + formal['provider_calls']
known = sum(c.get('cost_nano') or 0 for c in calls if c['usage_complete'])
unknown = sum(c['reserved_nano'] for c in calls if not c['usage_complete'])
assert all(c['complete'] for c in calls)
assert len(calls) <= auth['max_http_requests'] and known + unknown <= auth['max_estimated_nano']
phases = []
for phase, raw in [('preparation', prep), ('formal', formal)]:
    sql = read(OUT / f'live-{phase}-01/sql-postcheck.json')
    ledger = read(OUT / f'live-{phase}-01/ledger.json')
    settled = [c for c in sql['charges'] if c['status'] == 'settled']
    observed = [c for c in raw['provider_calls'] if c['usage_complete']]
    assert Counter((c['model'], c['prompt_tokens'], c['completion_tokens'], c['actual_nano']) for c in settled) == Counter(
        (c['request']['model'], c['input_tokens'], c['output_tokens'], c['cost_nano']) for c in observed)
    assert sql['pending_runs'] == ledger['pending_runs'] == 0
    phases.append({'phase': phase, 'http': len(raw['provider_calls']),
        'known_estimated_nano': sum(c['actual_nano'] for c in settled),
        'charge_statuses': dict(Counter(c['status'] for c in sql['charges'])),
        'unsettled_charges': [c for c in sql['charges'] if c['status'] != 'settled'],
        'budgets': sql['budgets'], 'run_statuses': sql['run_statuses'], 'pending_runs': 0,
        'meal_rows': sql['meal_rows'], 'proposal_rows': sql['proposal_rows']})
assert all(t['before'] == t['after'] for t in prep['tasks'])
assert all(turn['business_after'] == t['before'] for t in prep['tasks'] for turn in t['turns'])
assert phases[0]['meal_rows'] == 10 and phases[0]['proposal_rows'] == 0
assert phases[1]['meal_rows'] == phases[1]['proposal_rows'] == 0
reviews = read(OUT / 'live-preparation-01/reviews.json')
route = formal['reports'].get('route')
raw_meals = lines(OUT / 'live-formal-01/meal-cases.jsonl')
passes = []
for i in [1, 2]:
    rows = [r for r in raw_meals if r['task_id'].startswith(f'meal-p{i}:')]
    started = {r['task_id'] for r in rows} | {c['task_id'] for c in formal['provider_calls'] if c['task_id'].startswith(f'meal-p{i}:')}
    report_file = OUT / f'live-formal-01/meal-pass-{i}.json'
    original = read(report_file) if report_file.exists() else None
    passes.append({'pass': i, 'scheduled': 31, 'observed_runs': len(rows), 'started_cases': len(started),
        'unrun_cases': [j for j in range(1, 32) if f'meal-p{i}:{j}' not in started],
        'original_aggregate_report_available': original is not None,
        'original_aggregate_passed': original['passed'] if original else None, 'original_report': original})
metrics = read(OUT / 'live-preparation-01/metrics-reviewed.json')['groups'][0]
validation = {'created_at': datetime.now(timezone.utc).isoformat(), 'candidate': auth['candidate'],
    'scope': 'local original formal functions, with frozen external observation and aggregate caps',
    'github_protected_workflow_run': False, 'source_and_harness_unchanged': True,
    'preparation': {'scheduled_tasks': 5, 'turns': 9, 'collection_complete': all(t['collection_complete'] for t in prep['tasks']),
        'quality_outcomes': dict(Counter(r['outcome'] for r in reviews.values())),
        'registered_rest_scope_checks': {'advice_responses_with_active_plan': 4, 'correct_interval_and_values': 4,
            'R01_passed': reviews['serial-R01']['registered_rest_scope_checks_passed']},
        'new_quality_defects': ['P03 unsupported first-training inference from scoped weekly zero',
            'R01 unsupported individual recovery guarantee from planned rest duration'],
        'reviewer': 'implementer_manual_not_blind', 'metrics': metrics,
        'tracked_business_snapshots_unchanged': True,
        'individual_turns': [{'task': t['id'], 'turn': i+1, 'message': turn['message'],
            'visible_timing': turn['visible_timing'], 'status': turn['response']['status']}
            for t in prep['tasks'] for i, turn in enumerate(t['turns'])]},
    'route': {k: route[k] for k in ['total', 'passed_count', 'accuracy', 'structural_valid_count',
        'critical_total', 'critical_passed', 'ordinary_rules_fallbacks', 'passed']} if route else None,
    'meal_passes': passes, 'all_original_formal_checks_passed': formal['all_original_formal_checks_passed'],
    'overall_acceptance_passed': bool(formal['all_original_formal_checks_passed'] and all(r['outcome'] == 'completed' for r in reviews.values())),
    'budget': {'authorized_http': auth['max_http_requests'], 'http': len(calls),
        'authorized_nano': auth['max_estimated_nano'], 'known_estimated_nano': known,
        'unknown_provider_occupancy_nano': unknown, 'conservative_occupancy_nano': known + unknown,
        'provider_errors': sum(bool(c.get('error_type')) or c.get('status_code', 0) >= 400 for c in calls),
        'usage_complete': sum(c['usage_complete'] for c in calls),
        'basis': 'application conservative cache-miss tariff, not provider invoice',
        'previous_round_separate': auth['previous_round_separate']},
    'stopped': formal['stopped'] or prep['stopped'], 'phase_reconciliation': phases,
    'merged': False, 'deployed': False, 'phone_samples_added': 0,
    'limitations': ['Known synthetic regressions; no production success-rate claim',
        'R01 registered scope/value checks pass; recovery guarantee is a separately discovered quality defect',
        'One invalid timing is retained; no reconstructed five-turn total',
        'Phone case 5 retest, case 6 and timings still outstanding']}
(OUT / 'validation.json').write_text(json.dumps(validation, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'http': len(calls), 'known_cny': known / 1e9, 'unknown_cny': unknown / 1e9,
    'route_passed': validation['route']['passed'] if route else None,
    'meal_passes': [p['original_aggregate_passed'] for p in passes],
    'quality_outcomes': validation['preparation']['quality_outcomes'],
    'overall_acceptance_passed': validation['overall_acceptance_passed'], 'stopped': validation['stopped']}, ensure_ascii=False))
