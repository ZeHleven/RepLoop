from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('demo_trial_allowance', Path(__file__).with_name('run.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

BEFORE = {'agent_runs': [{'status': 'completed'}], 'demo_visits': [{}],
          'model_charges': [{'status': 'settled', 'actual_nano': 166744000}],
          'model_budgets': [{'spent_nano': 166744000, 'reserved_nano': 0}]}
RECEIPT = {'commit': 'original', 'fatal': None, 'reserved_nano': 0,
           'spent_nano': 166744000, 'provider_calls': 1}


def test_continuation_deducts_prior_spend_and_requests():
    assert module.trial_allowance(BEFORE, RECEIPT, 'corrected', 60) == (166744000, 1)


@pytest.mark.parametrize('case', ['same-candidate', 'unknown-usage', 'reserved', 'pending', 'cost-mismatch', 'count-mismatch', 'new-budget', 'exhausted'])
def test_inconsistent_or_unsettled_history_cannot_reset_allowance(case):
    before, receipt, commit = deepcopy(BEFORE), deepcopy(RECEIPT), 'corrected'
    if case == 'same-candidate': commit = 'original'
    if case == 'unknown-usage': receipt['fatal'] = 'provider_error_or_unknown_usage'
    if case == 'reserved': before['model_budgets'][0]['reserved_nano'] = 1
    if case == 'pending': before['agent_runs'][0]['status'] = 'queued'
    if case == 'cost-mismatch': receipt['spent_nano'] = 1
    if case == 'count-mismatch': receipt['provider_calls'] = 2
    if case == 'new-budget': receipt = None
    if case == 'exhausted':
        receipt['spent_nano'] = before['model_budgets'][0]['spent_nano'] = before['model_charges'][0]['actual_nano'] = 5_000_000_000
    with pytest.raises(ValueError):
        module.trial_allowance(before, receipt, commit, 60)
