"""Reconcile immutable live observations and original formal verdicts offline."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/preparation-release-acceptance-20261110'
SOURCE = ROOT / '.tmp/agent-prep-advice-20261110'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line]


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


auth = read(OUT / 'authorization.json')
prep = read(OUT / 'live-preparation-01/result.json')
formal = read(OUT / 'live-formal-01/result.json')
assert (OUT / 'live-formal-01/exit.json').exists(), 'Wait for all in-flight calls and cleanup'
assert prep['source_commit'] == formal['source_commit'] == auth['candidate']
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SOURCE, text=True).strip() == auth['candidate']
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SOURCE).strip()
freeze = read(OUT / 'preflight-freeze.json')
assert all(hashlib.sha256((SOURCE / p).read_bytes()).hexdigest() == h for p, h in freeze['source_files'].items())
assert all(hashlib.sha256((ROOT / 'scripts/prep_release_acceptance' / p).read_bytes()).hexdigest() == h for p, h in freeze['harness_files'].items())
phase_allowance = read(OUT / 'live-formal-01/phase-allowance.json')
assert phase_allowance == {'prior_http': 8, 'prior_nano': 51494000, 'remaining_http': 292, 'remaining_nano': 2948506000}
all_calls = prep['provider_calls'] + formal['provider_calls']
known = sum(c.get('cost_nano') or 0 for c in all_calls if c['usage_complete'])
unknown = sum(c['reserved_nano'] for c in all_calls if not c['usage_complete'])
assert len(all_calls) <= 300 and known + unknown <= 3_000_000_000
assert all(c['complete'] for c in all_calls)
phases = []
for name, raw in [('preparation', prep), ('formal', formal)]:
    ledger = read(OUT / f'live-{name}-01/ledger.json')
    calls = raw['provider_calls']
    charges = ledger['charges']
    settled = [c for c in charges if c['status'] == 'settled']
    assert Counter(c['actual_nano'] for c in settled) == Counter(c['cost_nano'] for c in calls if c['usage_complete'])
    assert ledger['pending_runs'] == 0
    phases.append({'phase': name, 'http': len(calls), 'charge_rows': len(charges),
                   'settled_charges': len(settled), 'status_counts': dict(Counter(c['status'] for c in charges)),
                   'known_estimated_nano': sum(c['actual_nano'] for c in settled),
                   'unsettled_charges': [c for c in charges if c['status'] != 'settled'],
                   'pending_runs': ledger['pending_runs']})
assert all(t['before'] == t['after'] for t in prep['tasks'])
assert all(t['business_after'] == task['before'] for task in prep['tasks'] for t in task['turns'])
formal_ledger = read(OUT / 'live-formal-01/ledger.json')
assert formal_ledger['meal_rows'] == formal_ledger['proposal_rows'] == 0
route = formal['reports'].get('route')
raw_meals = lines(OUT / 'live-formal-01/meal-cases.jsonl')
meal_passes = []
for index in [1, 2]:
    rows = [r for r in raw_meals if r['task_id'].startswith(f'meal-p{index}:')]
    started_ids = {r['task_id'] for r in rows} | {c['task_id'] for c in formal['provider_calls'] if c['task_id'].startswith(f'meal-p{index}:')}
    good = []
    for row in rows:
        artifacts = row['artifacts']
        fit = artifacts[0]['payload_data'].get('nutrition_fit', {}).get('status') if artifacts else None
        ok = row['run']['request_kind'] == 'generation' and row['run']['requested_effect'] == 'read' and row['run']['requested_output'] == 'daily_meal_plan' and fit in {'within_target', 'acceptable_deviation'}
        if ok:
            good.append(row['task_id'])
    path = OUT / f'live-formal-01/meal-pass-{index}.json'
    official = read(path) if path.exists() else None
    meal_passes.append({'pass': index, 'scheduled': 31, 'run_observations': len(rows), 'started_cases': len(started_ids),
                        'artifact_successes_observed': len(good), 'success_ids': good,
                        'incomplete_or_failed_ids': sorted(started_ids - set(good)),
                        'unrun_cases': [i for i in range(1,32) if f'meal-p{index}:{i}' not in started_ids],
                        'original_aggregate_report_available': official is not None,
                        'original_aggregate_passed': official['passed'] if official else None,
                        'original_aggregate_report': official,
                        'note': 'Observed successes are descriptive; an incomplete pass has no official pass verdict.'})
reviews = read(OUT / 'live-preparation-01/reviews.json')
validation = {
    'created_at': datetime.now(timezone.utc).isoformat(), 'candidate': auth['candidate'],
    'scope': 'local original release-eval functions with unchanged cases/scoring and external budget guard',
    'github_protected_workflow_run': False, 'source_and_harness_unchanged': True,
    'preflight_guard_tests_passed': 10,
    'preparation': {'scheduled': 2, 'core_advice_delivered': 2,
                    'quality_outcomes': dict(Counter(r['outcome'] for r in reviews.values())),
                    'visible_ms': [t['turns'][0]['visible_timing']['ms'] for t in prep['tasks']],
                    'tracked_business_snapshots_unchanged': True},
    'route': {k: route[k] for k in ['total', 'passed_count', 'accuracy', 'structural_valid_count', 'critical_total', 'critical_passed', 'ordinary_rules_fallbacks', 'passed']} if route else None,
    'meal_passes': meal_passes,
    'budget': {'authorized_http': 300, 'http': len(all_calls), 'known_estimated_nano': known,
               'unknown_provider_occupancy_nano': unknown, 'conservative_occupancy_nano': known + unknown,
               'authorized_nano': 3_000_000_000,
               'provider_errors': sum(bool(c.get('error_type')) or c.get('status_code', 0) >= 400 for c in all_calls),
               'usage_complete': sum(bool(c['usage_complete']) for c in all_calls),
               'pricing_basis': 'application conservative cache-miss tariff, not provider invoice',
               'previous_round_separate': auth['previous_round_separate']},
    'stopped': formal['stopped'] or prep['stopped'], 'phase_reconciliation': phases,
    'all_original_formal_checks_passed': formal['all_original_formal_checks_passed'],
    'overall_acceptance_passed': bool(formal['all_original_formal_checks_passed'] and all(r['outcome'] == 'completed' for r in reviews.values())),
    'deployed': False, 'merged': False, 'phone_samples_added': 0,
    'limits': ['Known development regressions reviewed by implementer, not blind',
               'Formal scripts ran locally, not in GitHub protected environment',
               'P01 rest interval scope wording remains inaccurate',
               'Phone case 5 retest and case 6 diet query/timings remain pending']
}
write('validation.json', validation)
print(json.dumps({'http': len(all_calls), 'known_cny': known / 1e9, 'unknown_provider_occupancy_cny': unknown / 1e9,
                  'stopped': validation['stopped'], 'route_passed': route['passed'] if route else None,
                  'meal_passes': [{k: p[k] for k in ['pass', 'run_observations', 'artifact_successes_observed', 'unrun_cases', 'original_aggregate_passed']} for p in meal_passes],
                  'quality_outcomes': validation['preparation']['quality_outcomes'],
                  'overall_acceptance_passed': validation['overall_acceptance_passed']}, ensure_ascii=False))
