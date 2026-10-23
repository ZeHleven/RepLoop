import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, func

from app.models.user import User
from app.models.agent import AgentRun, AgentMessage
from app.models.model_budget import ModelBudget, ModelCharge
from app.services import model_budget as budget
from app.services.auth import create_access_token

pytestmark = pytest.mark.asyncio
URL = "https://api.deepseek.com/chat/completions"
PAYLOAD = {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "合成测试"}]}
RAW = {"model": "deepseek-flash", "choices": [{"message": {"content": "合成回答"}, "finish_reason": "stop"}],
       "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}}


@pytest_asyncio.fixture
async def account(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(budget, "AsyncSessionLocal", session_factory)
    user = User(email=f"budget-{uuid.uuid4()}@example.com", password_hash="synthetic")
    db_session.add(user)
    await db_session.commit()
    return user.id


async def test_atomic_parallel_reservations(account, session_factory):
    attempts = await asyncio.gather(*[
        budget.reserve(account, "deepseek-flash", 1_000_000_000) for _ in range(20)
    ], return_exceptions=True)
    assert sum(isinstance(x, str) for x in attempts) == 5
    assert sum(isinstance(x, budget.ModelBudgetError) for x in attempts) == 15
    async with session_factory() as db:
        row = await db.get(ModelBudget, account)
        assert row.reserved_nano == budget.LIMIT_NANO
        assert await db.scalar(select(func.count()).select_from(ModelCharge).where(
            ModelCharge.user_id == account)) == 5


async def test_settlement_is_idempotent_and_survives_new_session(account, session_factory):
    charge = await budget.reserve(account, "deepseek-flash", 100_000)
    assert await budget.settle(charge, (60_000, 10, 5))
    assert await budget.settle(charge, (60_000, 10, 5))
    async with session_factory() as db:
        row = await db.get(ModelBudget, account)
        assert (row.spent_nano, row.reserved_nano) == (60_000, 0)
    assert (await budget.budget_status(account))["spent_yuan"] == "0.000060000"


async def test_unknown_usage_retains_reservation_and_blocks(account, session_factory):
    charge = await budget.reserve(account, "deepseek-flash", 100_000)
    assert not await budget.settle(charge, None)
    with pytest.raises(budget.ModelBudgetError, match="无法核实"):
        await budget.reserve(account, "deepseek-flash", 1)
    async with session_factory() as db:
        row = await db.get(ModelBudget, account)
        assert row.reserved_nano == 100_000 and row.blocked


async def test_different_accounts_have_separate_allowances(account, db_session):
    other = User(email=f"other-{uuid.uuid4()}@example.com", password_hash="synthetic")
    db_session.add(other)
    await db_session.commit()
    await budget.reserve(account, "deepseek-flash", budget.LIMIT_NANO)
    await budget.reserve(other.id, "deepseek-flash", budget.LIMIT_NANO)


async def test_reservation_survives_scope_change(account):
    with budget.billing_user(account):
        await budget.reserve(account, "deepseek-flash", budget.LIMIT_NANO)
    with budget.billing_user(account), pytest.raises(budget.ModelBudgetError):
        await budget.reserve(account, "deepseek-flash", 1)


async def test_metered_http_sends_bounded_request_and_settles(account):
    seen = []
    def provider(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=RAW)
    with budget.billing_user(account):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            response = await client.post(URL, json=PAYLOAD)
    assert response.json() == RAW
    assert seen[0]["max_tokens"] == 4096
    assert (await budget.budget_status(account))["spent_yuan"] == "0.000360000"
    assert (await budget.budget_status(account))["reserved_yuan"] == "0.000000000"


async def test_exhausted_never_reaches_provider(account):
    await budget.reserve(account, "deepseek-flash", budget.LIMIT_NANO)
    provider = AsyncMock()
    with budget.billing_user(account), pytest.raises(budget.ModelBudgetError):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            await client.post(URL, json=PAYLOAD)
    provider.assert_not_called()


@pytest.mark.parametrize("body", [
    {}, {**RAW, "usage": {}},
    {**RAW, "usage": {"prompt_tokens": 100, "completion_tokens": -1, "total_tokens": 99}},
    {**RAW, "usage": {"prompt_tokens": True, "completion_tokens": 1, "total_tokens": 2}},
    {**RAW, "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 100}},
    {**RAW, "model": "unpriced-flash"},
])
async def test_unverifiable_response_pauses(account, body):
    with budget.billing_user(account), pytest.raises(budget.ModelBudgetError):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(
                httpx.MockTransport(lambda r: httpx.Response(200, json=body)))) as client:
            await client.post(URL, json=PAYLOAD)
    state = await budget.budget_status(account)
    assert state["blocked"] and float(state["reserved_yuan"]) > 0


async def test_http_error_keeps_upper_bound_and_next_attempt_is_charged(account):
    responses = [httpx.Response(400, json={"error": "unsupported strict tool"}),
                 httpx.Response(200, json=RAW)]
    with budget.billing_user(account):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(
                httpx.MockTransport(lambda r: responses.pop(0)))) as client:
            assert (await client.post(URL, json=PAYLOAD)).status_code == 400
            assert (await client.post(URL, json=PAYLOAD)).status_code == 200
    state = await budget.budget_status(account)
    assert not state["blocked"] and float(state["spent_yuan"]) > 0.03


async def test_timeout_stops_retry_and_keeps_money(account):
    seen = []
    def provider(request):
        seen.append(request)
        raise httpx.ReadTimeout("synthetic", request=request)
    with budget.billing_user(account):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            for _ in range(2):
                with pytest.raises(budget.ModelBudgetError):
                    await client.post(URL, json=PAYLOAD)
    assert len(seen) == 1
    assert (await budget.budget_status(account))["blocked"]


@pytest.mark.parametrize("change", [
    {"stream": True}, {"model": "unpriced"}, {"n": 2},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.com"}]}]},
    {"tools": [{"type": "web_search"}]}, {"max_tokens": 0},
])
async def test_unsupported_request_fails_before_provider(account, change):
    provider = AsyncMock()
    with budget.billing_user(account), pytest.raises(budget.ModelBudgetError):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            await client.post(URL, json={**PAYLOAD, **change})
    provider.assert_not_called()
    assert (await budget.budget_status(account))["spent_yuan"] == "0.000000000"


async def test_missing_identity_fails_before_provider():
    provider = AsyncMock()
    with pytest.raises(budget.ModelBudgetError, match="计费账号"):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            await client.post(URL, json=PAYLOAD)
    provider.assert_not_called()


async def test_budget_endpoint_uses_authenticated_account(account, client):
    await budget.reserve(account, "deepseek-flash", 1_000_000_000)
    headers = {"Authorization": f"Bearer {create_access_token(account)}"}
    response = await client.get("/api/v1/agent/budget?user_id=someone-else", headers=headers)
    assert response.status_code == 200
    assert response.json()["remaining_yuan"] == "4.000000000"
    assert (await client.get("/api/v1/agent/budget")).status_code in (401, 403)


async def test_real_async_worker_reports_budget_denial(account, client, session_factory, monkeypatch):
    from app.config import settings
    from app.services.agent_jobs import claim_next_agent_run, process_agent_run
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic-unused-key")
    await budget.reserve(account, "deepseek-flash", budget.LIMIT_NANO)
    headers = {"Authorization": f"Bearer {create_access_token(account)}"}
    response = await client.post("/api/v1/agent/runs", headers=headers,
        json={"message": "我最近的训练进度怎么样", "client_request_id": str(uuid.uuid4())})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    async with session_factory() as db:
        assert await claim_next_agent_run(db) == run_id
    await process_agent_run(session_factory, run_id)
    result = await client.get(f"/api/v1/agent/runs/{run_id}", headers=headers)
    assert result.json()["status"] == "failed"
    assert result.json()["error_code"] == "model_budget_exhausted"
    assert "5元" in result.json()["error_message"]


async def test_legacy_chat_denial_is_clear(account, client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic-unused-key")
    await budget.reserve(account, "deepseek-flash", budget.LIMIT_NANO)
    response = await client.post("/api/v1/chat",
        headers={"Authorization": f"Bearer {create_access_token(account)}"},
        json={"message": "给我一般训练建议"})
    assert response.status_code == 402
    assert response.json()["code"] == "model_budget_exhausted"


async def test_provider_client_inventory_is_metered():
    service_dir = Path(__file__).parents[1] / "app" / "services"
    for name in ("agent_runtime.py", "agent_daily_meal_plans.py"):
        source = (service_dir / name).read_text()
        assert "model_http_client()" in source and "http_async_client" in source
    source = (service_dir / "ai_client.py").read_text()
    assert "httpx.AsyncClient()" not in source
    assert source.count("async with model_http_client() as client:") == 2


async def test_real_langchain_client_uses_metered_transport(account, monkeypatch):
    from app.config import settings
    from app.services import agent_runtime
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic")
    raw = {**RAW, "id": "synthetic-1", "object": "chat.completion", "created": 1,
           "choices": [{"index": 0, "message": {"role": "assistant", "content": "合成回答"},
                        "finish_reason": "stop"}]}
    transport = budget.BudgetTransport(httpx.MockTransport(lambda r: httpx.Response(200, json=raw)))
    async with httpx.AsyncClient(transport=transport) as client:
        monkeypatch.setattr(agent_runtime, "model_http_client", lambda: client)
        with budget.billing_user(account):
            reply = await agent_runtime._build_model().ainvoke("合成测试")
    assert reply.content == "合成回答"
    assert (await budget.budget_status(account))["spent_yuan"] == "0.000360000"


async def test_structured_fallback_counts_each_physical_request(account, monkeypatch):
    from app.config import settings
    from app.services.ai_client import structured_chat_completion
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic")
    seen = []
    def provider(request):
        seen.append(str(request.url))
        if len(seen) == 1:
            return httpx.Response(400, json={"error": "unsupported strict"})
        return httpx.Response(200, json={**RAW, "choices": [
            {"message": {"content": '{"value":"ok"}'}, "finish_reason": "stop"}]})
    client = httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider)))
    monkeypatch.setattr(budget, "model_http_client", lambda: client)
    with budget.billing_user(account):
        result = await structured_chat_completion(
            [{"role": "user", "content": "合成"}], model="deepseek-v4-flash",
            max_tokens=100, temperature=0, function_name="value",
            function_description="synthetic", json_schema={"type": "object"},
            json_example={"value": "ok"})
    assert result.payload == {"value": "ok"} and len(seen) == 2
    assert float((await budget.budget_status(account))["spent_yuan"]) > 0.00036


async def test_cancel_keeps_reservation(account):
    async def provider(request):
        raise asyncio.CancelledError()
    with budget.billing_user(account), pytest.raises(asyncio.CancelledError):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            await client.post(URL, json=PAYLOAD)
    state = await budget.budget_status(account)
    assert state["blocked"] and float(state["reserved_yuan"]) > 0


async def test_accounting_database_failure_sends_nothing(account, monkeypatch):
    monkeypatch.setattr(budget, "reserve", AsyncMock(side_effect=RuntimeError("synthetic DB outage")))
    provider = AsyncMock()
    with budget.billing_user(account), pytest.raises(budget.ModelBudgetError):
        async with httpx.AsyncClient(transport=budget.BudgetTransport(httpx.MockTransport(provider))) as client:
            await client.post(URL, json=PAYLOAD)
    provider.assert_not_called()


async def test_synchronous_agent_denial_is_clear(account, client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic-unused-key")
    await budget.reserve(account, "deepseek-flash", budget.LIMIT_NANO)
    response = await client.post("/api/v1/agent/chat",
        headers={"Authorization": f"Bearer {create_access_token(account)}"},
        json={"message": "我最近的训练进度怎么样"})
    assert response.status_code == 402
    assert response.json()["code"] == "model_budget_exhausted"
