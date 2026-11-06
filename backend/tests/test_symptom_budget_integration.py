"""R157: actual ASGI/worker, budget transport and PostgreSQL; synthetic provider only."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from evals.postgres_test_fixtures import db_session, engine, session_factory  # noqa: F401
from app.config import settings
from app.database import get_db
from app.main import app
from app.models.agent import AgentProposal, AgentRun
from app.models.model_budget import ModelBudget, ModelCharge
from app.models.user import User
from app.services import agent_runtime as runtime, agent_symptom_facts as facts
from app.services import model_budget as budget
from app.services.auth import create_access_token
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.agent_intent import IntentResolverOutcome
from test_symptom_scope_sql import seed, resolution

pytestmark = pytest.mark.asyncio
MESSAGE = "今天右膝盖下楼梯就疼，周一的计划怎么练"
URL = "https://api.deepseek.com/chat/completions"
PAYLOAD = {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "synthetic"}]}


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    # The imported engine creates a unique schema for each test.
    yield


@pytest.fixture
def scenario(monkeypatch, session_factory):
    state = {"mode": "valid", "http": []}

    def provider(request):
        body = json.loads(request.content)
        record = {"request": body}
        state["http"].append(record)
        assert len(state["http"]) <= 6, "unexpected provider retry"
        if state["mode"] == "timeout":
            record["exception"] = {"type": "ReadTimeout", "message": "synthetic-r157-timeout"}
            raise httpx.ReadTimeout("synthetic-r157-timeout", request=request)
        response = {
            "model": "deepseek-v4-flash",
            "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
        }
        if state["mode"] == "invalid_json":
            arguments = "invalid-json"
            response["choices"] = [{"message": {
                "content": arguments,
                "tool_calls": [{"function": {
                    "name": "submit_symptom_facts", "arguments": arguments,
                }}],
            }, "finish_reason": "stop"}]
        else:
            output = copy.deepcopy(facts.EXAMPLE)
            output["reports"] = [{
                "region": "knee", "side": "right", "site_text": "右膝盖",
                "quote": "今天右膝盖下楼梯就疼",
            }]
            response["choices"] = [{"message": {"tool_calls": [{"function": {
                "name": "submit_symptom_facts",
                "arguments": json.dumps(output, ensure_ascii=False),
            }}]}, "finish_reason": "stop"}]
        if state["mode"] == "missing_usage":
            response.pop("usage")
        record["response"] = response
        return httpx.Response(200, json=response)

    def model_client():
        return httpx.AsyncClient(
            transport=budget.BudgetTransport(httpx.MockTransport(provider)),
            trust_env=False,
        )

    async def database():
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    monkeypatch.setattr(budget, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(budget, "model_http_client", model_client)
    for name, value in {
        "DEEPSEEK_API_KEY": "synthetic-unused-r157",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "AGENT_ENABLED": True,
        "AGENT_PLANNED_EXECUTION_ENABLED": False,
        "AGENT_SYMPTOM_SCOPE_ENABLED": True,
        "AGENT_SYMPTOM_FACTS_MODEL_ENABLED": True,
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(runtime, "resolve_intent_with_fallback", AsyncMock(
        return_value=IntentResolverOutcome(resolution(MESSAGE), source="model", attempt_count=1),
    ))
    yield state
    app.dependency_overrides.clear()


async def invoke(client, factory, conversation_id, endpoint):
    data = {"conversation_id": conversation_id, "message": MESSAGE}
    if endpoint == "sync":
        response = await client.post("/api/v1/agent/chat", json=data)
    else:
        data["client_request_id"] = uuid4().hex
        accepted = await client.post("/api/v1/agent/runs", json=data)
        assert accepted.status_code == 202, accepted.text
        run_id = accepted.json()["run_id"]
        async with factory() as db:
            assert await claim_next_agent_run(db) == run_id
        await asyncio.wait_for(process_agent_run(factory, run_id), 15)
        response = await client.get("/api/v1/agent/runs/" + run_id)
    async with factory() as db:
        run = await db.scalar(select(AgentRun).where(
            AgentRun.conversation_id == conversation_id,
        ).order_by(AgentRun.queued_at.desc(), AgentRun.id.desc()).limit(1))
    return response, run


@pytest.mark.parametrize("failure", ["timeout", "missing_usage"])
@pytest.mark.parametrize("endpoint", ["sync", "worker"])
async def test_optional_facts_budget_failure_is_visible_on_current_run(
    db_session, session_factory, scenario, failure, endpoint,
):
    user, conversation = await seed(db_session)
    scenario["mode"] = failure
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer " + create_access_token(user.id)},
    ) as client:
        response, run = await invoke(client, session_factory, conversation.id, endpoint)

    async with session_factory() as db:
        row = await db.get(ModelBudget, user.id)
        charges = list((await db.scalars(select(ModelCharge).where(
            ModelCharge.user_id == user.id,
        ))).all())
        proposals = await db.scalar(select(func.count()).select_from(AgentProposal).where(
            AgentProposal.user_id == user.id,
        ))
    before_next = len(scenario["http"])
    with budget.billing_user(user.id), pytest.raises(budget.ModelBudgetError) as denied:
        async with budget.model_http_client() as client:
            await client.post(URL, json=PAYLOAD)
    assert denied.value.code == "model_budget_unavailable"
    assert len(scenario["http"]) == before_next

    other = User(email=f"r157-other-{uuid4().hex}@example.invalid", password_hash="synthetic")
    db_session.add(other)
    await db_session.commit()
    scenario["mode"] = "valid"
    with budget.billing_user(other.id):
        async with budget.model_http_client() as client:
            assert (await client.post(URL, json=PAYLOAD)).status_code == 200

    evidence = {
        "endpoint": endpoint, "failure": failure,
        "http_status": response.status_code, "run_status": run.status,
        "error_code": run.error_code, "response": response.json(),
        "blocked": row.blocked, "reserved_nano": row.reserved_nano,
        "spent_nano": row.spent_nano, "charge_statuses": [c.status for c in charges],
        "proposals": proposals, "same_account_next_provider_calls": 0,
        "other_account_mock_http_status": 200, "http": scenario["http"],
    }
    print("R157_HTTP_SQL_EVIDENCE=" + json.dumps(evidence, ensure_ascii=True))
    assert row.blocked and row.reserved_nano > 0 and row.spent_nano == 0
    assert len(charges) == 1 and charges[0].status == "uncertain"
    assert proposals == 0
    assert run.status == "failed", "optional facts hid an account-level budget refusal"
    assert run.error_code == "model_budget_unavailable"
    if endpoint == "sync":
        assert response.status_code == 503
        assert response.json()["code"] == "model_budget_unavailable"
    else:
        assert response.json()["status"] == "failed"
        assert "费用" in response.json()["error_message"]


async def test_known_usage_invalid_facts_can_still_fall_back_to_rules(
    db_session, session_factory, scenario,
):
    user, conversation = await seed(db_session)
    scenario["mode"] = "invalid_json"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": "Bearer " + create_access_token(user.id)},
    ) as client:
        response, run = await invoke(client, session_factory, conversation.id, "worker")
    async with session_factory() as db:
        row = await db.get(ModelBudget, user.id)
        charges = list((await db.scalars(select(ModelCharge).where(
            ModelCharge.user_id == user.id,
        ))).all())
    print("R157_PARSE_FALLBACK=" + json.dumps({
        "run_status": run.status, "error_code": run.error_code,
        "blocked": row.blocked, "reserved_nano": row.reserved_nano,
        "spent_nano": row.spent_nano, "charge_statuses": [c.status for c in charges],
        "http": scenario["http"],
    }, ensure_ascii=True))
    assert run.status == "completed" and run.error_code is None
    assert not row.blocked and row.reserved_nano == 0 and row.spent_nano > 0
    assert len(charges) == 2 and all(c.status == "settled" for c in charges)
    assert response.json()["execution_trace"]["termination_reason"] == "symptom_deterministic_scope"
