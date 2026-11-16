"""Exercise the release evaluator through the real ledger, without network."""
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models.user import User
from app.services import model_budget as budget
from app.services.agent_intent import IntentResolution, IntentResolverOutcome
from scripts import evaluate_agent_routing_real as evaluator

URL = 'https://api.deepseek.com/chat/completions'
PAYLOAD = {'model': 'deepseek-v4-flash', 'messages': [{'role': 'user', 'content': 'synthetic'}]}
RAW = {'model': 'deepseek-flash', 'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}


def outcome(failed=False):
    return IntentResolverOutcome(
        resolution=IntentResolution(primary_intent='profile_query', intent_domain='profile',
            request_kind='query', requested_effect='read', requested_output='answer', risk_level='low', confidence=.9),
        source='rules' if failed else 'model', attempt_count=1,
        understanding_failed=failed, error_category='ModelBudgetError' if failed else None)


def configure(monkeypatch, session_factory):
    monkeypatch.setattr(evaluator, 'AsyncSessionLocal', session_factory, raising=False)
    monkeypatch.setattr(budget, 'AsyncSessionLocal', session_factory)
    monkeypatch.setattr(evaluator.settings, 'DATABASE_URL', 'postgresql+asyncpg://synthetic:synthetic@localhost/route_eval_test')
    monkeypatch.setattr(evaluator, 'CASES', evaluator.CASES[:1])


@pytest.mark.asyncio
async def test_evaluator_commits_identity_and_settles_real_transport(monkeypatch, session_factory):
    configure(monkeypatch, session_factory)
    identities, requests = [], []

    async def provider(request):
        requests.append(request)
        return httpx.Response(200, json=RAW)

    async def resolve(*args, **kwargs):
        scope = budget._scope.get()
        identities.append(scope.user_id if scope else None)
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            await client.post(URL, json=PAYLOAD)
        return outcome()

    monkeypatch.setattr(evaluator, 'resolve_intent_with_fallback', resolve)
    result = await evaluator.evaluate()
    assert result['passed'] and len(requests) == 1
    assert identities[0] and budget._scope.get() is None
    async with session_factory() as db:
        assert (await db.get(User, identities[0])).email.endswith('@example.com')
    assert result['billing']['request_reservations'] == 1
    assert result['billing']['complete'] and result['billing']['estimated_cny'] == .00036
    assert identities[0] not in str(result)


@pytest.mark.asyncio
async def test_unknown_usage_keeps_one_account_blocked_for_later_cases(monkeypatch, session_factory):
    configure(monkeypatch, session_factory)
    monkeypatch.setattr(evaluator, 'CASES', evaluator.CASES * 2)
    identities, requests = [], []

    async def provider(request):
        requests.append(request)
        return httpx.Response(200, json={'model': 'deepseek-flash'})

    async def resolve(*args, **kwargs):
        scope = budget._scope.get()
        identities.append(scope.user_id if scope else None)
        try:
            async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
                await client.post(URL, json=PAYLOAD)
        except budget.ModelBudgetError:
            return outcome(failed=True)
        raise AssertionError('Missing usage must not be accepted')

    monkeypatch.setattr(evaluator, 'resolve_intent_with_fallback', resolve)
    result = await evaluator.evaluate()
    assert len(requests) == 1 and len(set(identities)) == 1 and identities[0]
    assert not result['passed'] and not result['billing']['complete']
    assert result['billing']['estimated_cny'] is None and result['billing']['blocked']
    assert result['billing']['reserved_cny'] > 0
    assert result['billing']['status_counts'] == {'uncertain': 1}
    assert budget._scope.get() is None


@pytest.mark.parametrize('url', [
    'postgresql+asyncpg://synthetic:synthetic@remote.example.com/route_eval_test',
    'postgresql+asyncpg://synthetic:synthetic@localhost/production',
    'sqlite+aiosqlite:///:memory:',
])
@pytest.mark.asyncio
async def test_nonisolated_database_is_rejected_before_seeding_or_model(monkeypatch, url):
    monkeypatch.setattr(evaluator.settings, 'DATABASE_URL', url)
    factory = AsyncMock(side_effect=AssertionError('Must not open database'))
    resolver = AsyncMock(side_effect=AssertionError('Must not invoke model'))
    monkeypatch.setattr(evaluator, 'AsyncSessionLocal', factory, raising=False)
    monkeypatch.setattr(evaluator, 'resolve_intent_with_fallback', resolver)
    with pytest.raises(RuntimeError, match='isolated'):
        await evaluator.evaluate()
    factory.assert_not_called()
    resolver.assert_not_called()
