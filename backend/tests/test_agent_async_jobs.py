import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import func, select

from app.config import settings
from app.models.agent import (
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolCall,
)
from app.services.agent_jobs import claim_next_agent_run, process_agent_run
from app.services.agent_runtime import AgentRunOwnershipLost, execute_agent_run
from app.services.agent_intent import (
    IntentResolverOutcome,
    normalize_resolution,
    resolve_intent,
)


@pytest.fixture(autouse=True)
def deterministic_intent_for_job_lifecycle_tests():
    async def resolve(message, **_kwargs):
        return IntentResolverOutcome(
            resolution=normalize_resolution(message, resolve_intent(message)),
            source="model",
            attempt_count=1,
        )

    with patch(
        "app.services.agent_runtime.resolve_intent_with_fallback",
        new=resolve,
    ):
        yield


async def _token(client, email: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    return response.json()["access_token"]


@pytest.mark.asyncio
async def test_async_agent_submission_is_idempotent_and_pollable(
    client,
    db_session,
    session_factory,
):
    token = await _token(client, "agent-async@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": "我的训练目标是什么？",
        "client_request_id": "async-request-0001",
    }

    first = await client.post("/api/v1/agent/runs", json=payload, headers=headers)
    duplicate = await client.post("/api/v1/agent/runs", json=payload, headers=headers)

    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert duplicate.json()["run_id"] == first.json()["run_id"]
    assert duplicate.json()["conversation_id"] == first.json()["conversation_id"]
    run_id = first.json()["run_id"]

    message_count = await db_session.scalar(
        select(func.count(AgentMessage.id)).where(AgentMessage.run_id == run_id)
    )
    assert message_count == 1

    queued = await client.get(f"/api/v1/agent/runs/{run_id}", headers=headers)
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert queued.json()["reply"] is None

    claimed_run_id = await claim_next_agent_run(db_session)
    assert claimed_run_id == run_id

    mocked_result = {
        "messages": [
            HumanMessage(content="我的训练目标是什么？"),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "profile_get_summary",
                    "args": {},
                    "id": "async-tool-1",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({"found": True, "primary_goal": "增肌"}),
                tool_call_id="async-tool-1",
                name="profile_get_summary",
            ),
            AIMessage(content="你当前保存的训练目标是增肌。"),
        ]
    }
    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(return_value=mocked_result),
        ),
    ):
        await process_agent_run(session_factory, run_id)

    completed = await client.get(f"/api/v1/agent/runs/{run_id}", headers=headers)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["reply"] == "你当前保存的训练目标是增肌。"
    assert completed.json()["cards"][0]["type"] == "profile.get_summary"
    assert completed.json()["poll_after_ms"] is None

    after_completion = await client.post(
        "/api/v1/agent/runs",
        json=payload,
        headers=headers,
    )
    assert after_completion.json()["run_id"] == run_id
    assert after_completion.json()["status"] == "completed"

    conflicting = await client.post(
        "/api/v1/agent/runs",
        json={**payload, "message": "换成另一条消息"},
        headers=headers,
    )
    assert conflicting.status_code == 409


@pytest.mark.asyncio
async def test_async_submission_persists_artifact_card_action_for_worker(
    client,
    db_session,
):
    token = await _token(client, "agent-artifact-action@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": "保存这份方案",
        "client_request_id": "artifact-action-request-0001",
        "artifact_action": {
            "action": "save_as_proposal",
            "artifact_id": "artifact-123",
            "expected_version": 1,
            "payload_fingerprint": "a" * 64,
        },
    }

    response = await client.post("/api/v1/agent/runs", json=payload, headers=headers)
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    stored = await db_session.scalar(select(AgentMessage).where(
        AgentMessage.run_id == run_id,
        AgentMessage.role == "user",
    ))
    assert stored is not None
    assert stored.content_data["artifact_action"] == payload["artifact_action"]

    replay = await client.post("/api/v1/agent/runs", json=payload, headers=headers)
    assert replay.status_code == 202
    assert replay.json()["run_id"] == run_id

    conflict = await client.post(
        "/api/v1/agent/runs",
        json={
            **payload,
            "artifact_action": {
                **payload["artifact_action"],
                "artifact_id": "artifact-456",
            },
        },
        headers=headers,
    )
    assert conflict.status_code == 409
    run = await db_session.get(AgentRun, run_id)
    assert run is not None
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    await db_session.commit()


@pytest.mark.asyncio
async def test_concurrent_idempotent_submissions_share_one_run_and_conversation(
    client,
    db_session,
):
    token = await _token(client, "agent-concurrent-idempotency@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": "并发提交也只创建一次",
        "client_request_id": "concurrent-idempotency-request",
    }

    first, second = await asyncio.gather(
        client.post("/api/v1/agent/runs", json=payload, headers=headers),
        client.post("/api/v1/agent/runs", json=payload, headers=headers),
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["run_id"] == second.json()["run_id"]
    assert first.json()["conversation_id"] == second.json()["conversation_id"]
    run_count = await db_session.scalar(
        select(func.count(AgentRun.id)).where(
            AgentRun.idempotency_key == payload["client_request_id"]
        )
    )
    conversation_count = await db_session.scalar(
        select(func.count(AgentConversation.id)).where(
            AgentConversation.id == first.json()["conversation_id"]
        )
    )
    assert run_count == 1
    assert conversation_count == 1
    run_id = first.json()["run_id"]
    assert await claim_next_agent_run(db_session) == run_id
    run = await db_session.get(AgentRun, run_id)
    assert run is not None
    run.status = "completed"
    run.completed_at = datetime.now(timezone.utc)
    run.lease_expires_at = None
    await db_session.commit()


@pytest.mark.asyncio
async def test_stale_running_agent_job_is_reclaimed(client, db_session):
    token = await _token(client, "agent-reclaim@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "查看当前计划",
            "client_request_id": "async-request-reclaim",
        },
        headers=headers,
    )
    run_id = response.json()["run_id"]

    first_claim = await claim_next_agent_run(db_session)
    assert first_claim == run_id
    run = await db_session.get(AgentRun, run_id)
    assert run is not None
    assert run.attempt_count == 1
    run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    second_claim = await claim_next_agent_run(db_session)
    assert second_claim == run_id
    await db_session.refresh(run)
    assert run.status == "running"
    assert run.attempt_count == 2
    assert run.lease_expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_agent_runs_are_serialized_within_a_conversation(client, db_session):
    token = await _token(client, "agent-serialized@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "先查计划",
            "client_request_id": "serialized-request-1",
        },
        headers=headers,
    )
    conversation_id = first.json()["conversation_id"]
    second = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "再查进度",
            "conversation_id": conversation_id,
            "client_request_id": "serialized-request-2",
        },
        headers=headers,
    )

    first_run_id = await claim_next_agent_run(db_session)
    assert first_run_id == first.json()["run_id"]
    assert await claim_next_agent_run(db_session) is None

    first_run = await db_session.get(AgentRun, first_run_id)
    assert first_run is not None
    first_run.status = "completed"
    first_run.completed_at = datetime.now(timezone.utc)
    first_run.lease_expires_at = None
    await db_session.commit()

    assert await claim_next_agent_run(db_session) == second.json()["run_id"]


@pytest.mark.asyncio
async def test_later_queued_message_does_not_pollute_earlier_run_history(
    client,
    db_session,
    session_factory,
):
    token = await _token(client, "agent-history-order@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "先回答第一个问题",
            "client_request_id": "history-order-1",
        },
        headers=headers,
    )
    second = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "这是稍后排队的第二个问题",
            "conversation_id": first.json()["conversation_id"],
            "client_request_id": "history-order-2",
        },
        headers=headers,
    )
    invoke_agent = AsyncMock(side_effect=[
        {"messages": [AIMessage(content="第一个问题的回答")]},
        {"messages": [AIMessage(content="第二个问题的回答")]},
    ])

    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=invoke_agent,
        ),
    ):
        assert await claim_next_agent_run(db_session) == first.json()["run_id"]
        await process_agent_run(session_factory, first.json()["run_id"])

        first_history = invoke_agent.await_args_list[0].kwargs["history"]
        assert all(
            item["content"] != "这是稍后排队的第二个问题"
            for item in first_history
        )

        assert await claim_next_agent_run(db_session) == second.json()["run_id"]
        await process_agent_run(session_factory, second.json()["run_id"])

    second_history = invoke_agent.await_args_list[1].kwargs["history"]
    assert [item["content"] for item in second_history] == [
        "先回答第一个问题",
        "第一个问题的回答",
    ]


@pytest.mark.asyncio
async def test_worker_renews_lease_while_agent_execution_is_running(
    client,
    db_session,
    session_factory,
    monkeypatch,
):
    import json, os, time
    from pathlib import Path
    from app.services import agent_jobs as jobs
    # This test owns heartbeat scheduling and real SQL persistence, not host
    # wall-clock stability. Retained R60 diagnostics show the database clock
    # moving backwards between claim and renew; greatest correctly kept the
    # deadline, invalidating the old strictly-later-after-1.1s assumption.
    # R23 separately tests the shared database clock and backward correction.
    clock_origin = await jobs._lease_now(db_session)
    monotonic_origin = time.monotonic()
    async def forward_clock(_db):
        return clock_origin + timedelta(seconds=time.monotonic()-monotonic_origin)
    monkeypatch.setattr(jobs,'_lease_now',forward_clock)
    renewals=[]
    original_renew=jobs.renew_agent_run_lease
    async def observed_renew(db,**kwargs):
        stamp=time.monotonic()
        before=await jobs._lease_now(db)
        updated=await original_renew(db,**kwargs)
        stored=await db.scalar(select(AgentRun.lease_expires_at).where(AgentRun.id==kwargs['run_id']))
        renewals.append({'elapsed_monotonic':time.monotonic()-stamp,'db_before':before,'db_after':await jobs._lease_now(db),'stored_deadline':stored,'owned':updated})
        return updated
    monkeypatch.setattr(jobs,'renew_agent_run_lease',observed_renew)
    token = await _token(client, "agent-heartbeat@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "慢一点回答这个问题",
            "client_request_id": "heartbeat-request",
        },
        headers=headers,
    )
    run_id = response.json()["run_id"]
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_agent(*_args, **_kwargs):
        started.set()
        await release.wait()
        return {"messages": [AIMessage(content="慢请求已完成")]}

    with (
        patch.object(settings, "AGENT_RUN_LEASE_SECONDS", 1),
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=slow_agent,
        ),
    ):
        assert await claim_next_agent_run(db_session) == run_id
        run = await db_session.get(AgentRun, run_id)
        assert run is not None
        first_expiry = run.lease_expires_at
        task = asyncio.create_task(process_agent_run(session_factory, run_id))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            running_response = await client.get(
                f"/api/v1/agent/runs/{run_id}",
                headers=headers,
            )
            assert running_response.json()["execution_mode"] == "direct"
            assert running_response.json()["execution_trace"]["status"] == (
                "running"
            )
            assert running_response.json()["execution_trace"][
                "terminal_action"
            ] is None
            await asyncio.sleep(1.1)
            await db_session.refresh(run)
            assert run.lease_expires_at > first_expiry, {'first':first_expiry,'observed':run.lease_expires_at,'renewals':renewals}
            assert run.lease_expires_at > await jobs._lease_now(db_session)
            assert await claim_next_agent_run(db_session) is None
        finally:
            release.set()
            await task
            directory=os.getenv('R60_DIAGNOSTICS_DIR') or os.getenv('R57_DIAGNOSTICS_DIR')
            if directory:
                Path(directory,f'lease-diagnostic-{run_id}.json').write_text(json.dumps({'first_expiry':first_expiry,'renewals':renewals},default=str,indent=2),encoding='utf8')

    await db_session.refresh(run)
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_stale_attempt_cannot_write_duplicate_messages_or_tool_audits(
    client,
    db_session,
    session_factory,
):
    token = await _token(client, "agent-stale-owner@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "我的训练目标是什么？",
            "client_request_id": "stale-owner-request",
        },
        headers=headers,
    )
    run_id = response.json()["run_id"]
    assert await claim_next_agent_run(db_session) == run_id
    run = await db_session.get(AgentRun, run_id)
    assert run is not None
    stale_attempt_count = run.attempt_count
    run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()
    assert await claim_next_agent_run(db_session) == run_id
    await db_session.refresh(run)
    assert run.attempt_count == stale_attempt_count + 1

    conversation = await db_session.get(AgentConversation, run.conversation_id)
    user_message = await db_session.scalar(
        select(AgentMessage).where(
            AgentMessage.run_id == run_id,
            AgentMessage.role == "user",
        )
    )
    assert conversation is not None
    assert user_message is not None

    stale_invoke = AsyncMock(return_value={
        "messages": [AIMessage(content="过期 worker 的回答")],
    })
    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=stale_invoke,
        ),
        pytest.raises(AgentRunOwnershipLost),
    ):
        await execute_agent_run(
            db_session,
            run=run,
            conversation=conversation,
            user_message=user_message.content,
            expected_attempt_count=stale_attempt_count,
        )
    stale_invoke.assert_not_awaited()

    recovered_result = {
        "messages": [
            HumanMessage(content="我的训练目标是什么？"),
            AIMessage(
                content="",
                tool_calls=[{
                    "name": "profile_get_summary",
                    "args": {},
                    "id": "recovered-profile-call",
                    "type": "tool_call",
                }],
            ),
            ToolMessage(
                content=json.dumps({"found": True, "primary_goal": "增肌"}),
                tool_call_id="recovered-profile-call",
                name="profile_get_summary",
            ),
            AIMessage(content="恢复后的唯一回答"),
        ]
    }
    with (
        patch.object(settings, "AGENT_INTENT_MODEL_ENABLED", False),
        patch(
            "app.services.agent_runtime.invoke_langchain_agent",
            new=AsyncMock(return_value=recovered_result),
        ),
    ):
        await process_agent_run(session_factory, run_id)
        await process_agent_run(session_factory, run_id)

    assistant_count = await db_session.scalar(
        select(func.count(AgentMessage.id)).where(
            AgentMessage.run_id == run_id,
            AgentMessage.role == "assistant",
        )
    )
    tool_call_count = await db_session.scalar(
        select(func.count(AgentToolCall.id)).where(
            AgentToolCall.run_id == run_id
        )
    )
    assert assistant_count == 1
    assert tool_call_count == 1


@pytest.mark.asyncio
async def test_async_agent_run_is_private_to_owner(client):
    owner_token = await _token(client, "agent-async-owner@example.com")
    other_token = await _token(client, "agent-async-other@example.com")
    created = await client.post(
        "/api/v1/agent/runs",
        json={
            "message": "普通健身问题",
            "client_request_id": "async-request-private",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    response = await client.get(
        f"/api/v1/agent/runs/{created.json()['run_id']}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert response.status_code == 404
