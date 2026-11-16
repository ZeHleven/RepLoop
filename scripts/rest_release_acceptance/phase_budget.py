"""A single allowance is consumed sequentially; a stopped phase cannot resume."""
MAX_HTTP = 300
MAX_NANO = 4_000_000_000


def remaining_budget(prior_results):
    calls, occupied = 0, 0
    for result in prior_results:
        rows = result['provider_calls']
        ids = [row['id'] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate_provider_request')
        for row in rows:
            if not row.get('complete') or not row.get('usage_complete') or row.get('status_code') != 200 or row.get('error_type'):
                raise ValueError('prior_provider_error_or_unknown_usage')
            amount = row['cost_nano']
            if type(amount) is not int or amount < 0 or amount > row['reserved_nano']:
                raise ValueError('unverified_cost')
            occupied += amount
        if result.get('stopped'):
            raise ValueError('prior_phase_stopped')
        calls += len(rows)
    if calls >= MAX_HTTP or occupied >= MAX_NANO:
        raise ValueError('allowance_exhausted')
    return {'prior_http': calls, 'prior_nano': occupied,
            'remaining_http': MAX_HTTP - calls, 'remaining_nano': MAX_NANO - occupied}
