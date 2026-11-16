import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent / 'observer'))
from capture import ProviderCapture, RUN_CONTEXT
from phase_budget import remaining_budget
from app.services import model_budget as budget


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario', ['request_cap', 'cost_cap', 'http_400', 'unknown_usage'])
async def test_actual_transport_stops_across_users(monkeypatch, tmp_path, scenario):
    forwarded = []
    statuses = []

    async def reserve(*args):
        return 'synthetic-charge'

    async def settle(charge, actual):
        statuses.append('settled' if actual is not None else 'uncertain')
        return actual is not None

    async def error(charge):
        statuses.append('http_error_estimate')

    def handle(request):
        forwarded.append(request)
        if scenario == 'http_400':
            return httpx.Response(400, json={'error': 'offline_protocol_error'})
        data = {'model': 'deepseek-v4-flash', 'choices': []}
        if scenario != 'unknown_usage':
            data['usage'] = {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12}
        return httpx.Response(200, json=data)

    monkeypatch.setattr(budget, 'reserve', reserve)
    monkeypatch.setattr(budget, 'settle', settle)
    monkeypatch.setattr(budget, 'record_http_error', error)
    original = budget.BudgetTransport.__init__
    monkeypatch.setattr(budget.BudgetTransport, '__init__', original)
    capture = ProviderCapture(tmp_path, mode='live', max_requests=1 if scenario == 'request_cap' else 5,
                              max_cny=0.000001 if scenario == 'cost_cap' else 3)
    capture.install()
    token = RUN_CONTEXT.set({'task_id': 'offline-probe', 'run_id': None, 'worker_attempt': 1})
    try:
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(handle))) as client:
            for user in ['user-one', 'different-user-same-global-cap']:
                with budget.billing_user(user):
                    try:
                        await client.post('https://api.deepseek.com/chat/completions', json={
                            'model': 'deepseek-v4-flash', 'messages': [{'role': 'user', 'content': 'synthetic'}]})
                    except budget.ModelBudgetError:
                        pass
    finally:
        RUN_CONTEXT.reset(token)
    assert len(forwarded) == (0 if scenario == 'cost_cap' else 1)
    assert len(capture.calls) == len(forwarded)
    assert capture.fatal is not None
    if scenario in {'http_400', 'unknown_usage'}:
        assert capture.reserved_nano > 0 and capture.spent_nano == 0
    if scenario == 'request_cap':
        assert capture.spent_nano == 36000 and capture.reserved_nano == 0
    events = (tmp_path / 'provider-events.jsonl')
    if events.exists():
        assert 'authorization' not in events.read_text().lower()


def row(cost=100_000_000):
    return {'id': 'http-1', 'complete': True, 'usage_complete': True, 'status_code': 200,
            'error_type': None, 'cost_nano': cost, 'reserved_nano': 200_000_000}


def test_phase_handoff_never_resets_global_allowance():
    value = remaining_budget([{'provider_calls': [row()], 'stopped': None}])
    assert value == {'prior_http': 1, 'prior_nano': 100_000_000,
                     'remaining_http': 299, 'remaining_nano': 2_900_000_000}


@pytest.mark.parametrize('mutation', ['http_error', 'unknown', 'fatal', 'duplicate'])
def test_incomplete_or_stopped_phase_cannot_start_next(mutation):
    prior = {'provider_calls': [row()], 'stopped': None}
    if mutation == 'http_error': prior['provider_calls'][0]['status_code'] = 400
    if mutation == 'unknown': prior['provider_calls'][0]['usage_complete'] = False
    if mutation == 'fatal': prior['stopped'] = 'request_or_cost_budget'
    if mutation == 'duplicate': prior['provider_calls'].append(row())
    with pytest.raises(ValueError):
        remaining_budget([prior])


def test_full_request_allowance_cannot_restart():
    prior = {'provider_calls': [{**row(0), 'id': f'http-{i}'} for i in range(300)]}
    with pytest.raises(ValueError, match='allowance_exhausted'):
        remaining_budget([prior])
