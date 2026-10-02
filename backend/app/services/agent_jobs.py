from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, or_, select, update, func, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.agent import AgentConversation, AgentMessage, AgentRun
from app.services.agent_runtime import AgentRunOwnershipLost, execute_agent_run
from app.services.ai_client import AIServiceError
from app.services.agent_run_order import precedes


logger = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class EnqueuedAgentRun:
    run: AgentRun
    conversation: AgentConversation
    created: bool


class AgentIdempotencyConflict(Exception):
    pass


async def _validate_idempotent_replay(
    db: AsyncSession,
    *,
    run: AgentRun,
    user_message: str,
    conversation: AgentConversation | None,
    artifact_action: dict | None,
    clarification_action: dict | None,
) -> None:
    stored_message = await db.scalar(
        select(AgentMessage).where(
            AgentMessage.run_id == run.id,
            AgentMessage.role == "user",
        )
    )
    if stored_message is None or stored_message.content != user_message:
        raise AgentIdempotencyConflict(
            "client_request_id 已被另一条消息使用"
        )
    if (
        (stored_message.content_data or {}).get("artifact_action")
        != artifact_action
    ):
        raise AgentIdempotencyConflict(
            "client_request_id 已被另一项卡片操作使用"
        )
    if (
        (stored_message.content_data or {}).get("clarification_action")
        != clarification_action
    ):
        raise AgentIdempotencyConflict(
            "client_request_id 已被另一项澄清选择使用"
        )
    if conversation is not None and run.conversation_id != conversation.id:
        raise AgentIdempotencyConflict(
            "client_request_id 已被另一会话使用"
        )


async def enqueue_agent_run(
    db: AsyncSession,
    *,
    user_id: str,
    user_message: str,
    client_request_id: str,
    conversation: AgentConversation | None,
    artifact_action: dict | None = None,
    clarification_action: dict | None = None,
) -> EnqueuedAgentRun:
    requested_conversation = conversation
    existing = await db.scalar(
        select(AgentRun).where(
            AgentRun.user_id == user_id,
            AgentRun.idempotency_key == client_request_id,
        )
    )
    if existing is not None:
        await _validate_idempotent_replay(
            db,
            run=existing,
            user_message=user_message,
            conversation=requested_conversation,
            artifact_action=artifact_action,
            clarification_action=clarification_action,
        )
        existing_conversation = await db.get(
            AgentConversation,
            existing.conversation_id,
        )
        if existing_conversation is None:
            raise RuntimeError("Agent run references a missing conversation")
        logger.info(
            "Agent run replayed: run_id=%s status=%s",
            existing.id,
            existing.status,
        )
        return EnqueuedAgentRun(existing, existing_conversation, False)

    if conversation is None:
        conversation = AgentConversation(
            user_id=user_id,
            title=user_message[:50],
        )
        db.add(conversation)
        await db.flush()

    # Serialize allocation with the conversation row, including across workers.
    await db.execute(select(AgentConversation.id).where(
        AgentConversation.id == conversation.id, AgentConversation.user_id == user_id,
    ).with_for_update())
    # Another request may have committed the same key while we waited for the lock.
    existing = await db.scalar(select(AgentRun).where(
        AgentRun.user_id == user_id, AgentRun.idempotency_key == client_request_id,
    ))
    if existing is not None:
        await _validate_idempotent_replay(db, run=existing, user_message=user_message,
            conversation=requested_conversation, artifact_action=artifact_action,
            clarification_action=clarification_action)
        await db.commit()
        return EnqueuedAgentRun(existing, conversation, False)
    position = (await db.scalar(select(func.max(AgentRun.queue_position)).where(
        AgentRun.conversation_id == conversation.id, AgentRun.user_id == user_id,
    )) or 0) + 1
    run = AgentRun(
        conversation_id=conversation.id,
        user_id=user_id,
        status="queued",
        idempotency_key=client_request_id,
        model_name=settings.AGENT_MODEL,
        queue_position=position,
    )
    db.add(run)

    try:
        await db.flush()
        db.add(AgentMessage(
            conversation_id=conversation.id,
            run_id=run.id,
            role="user",
            content=user_message,
            content_data={
                "client_request_id": client_request_id,
                **(
                    {"artifact_action": artifact_action}
                    if artifact_action is not None
                    else {}
                ),
                **(
                    {"clarification_action": clarification_action}
                    if clarification_action is not None
                    else {}
                ),
            },
        ))
        conversation.updated_at = datetime.now(timezone.utc)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(
            select(AgentRun).where(
                AgentRun.user_id == user_id,
                AgentRun.idempotency_key == client_request_id,
            )
        )
        if existing is None:
            raise
        await _validate_idempotent_replay(
            db,
            run=existing,
            user_message=user_message,
            conversation=requested_conversation,
            artifact_action=artifact_action,
            clarification_action=clarification_action,
        )
        existing_conversation = await db.get(
            AgentConversation,
            existing.conversation_id,
        )
        if existing_conversation is None:
            raise RuntimeError("Agent run references a missing conversation")
        logger.info(
            "Agent run replayed after conflict: run_id=%s status=%s",
            existing.id,
            existing.status,
        )
        return EnqueuedAgentRun(existing, existing_conversation, False)

    await db.refresh(run)
    logger.info("Agent run queued: run_id=%s", run.id)
    return EnqueuedAgentRun(run, conversation, True)


async def _lease_now(db: AsyncSession) -> datetime:
    # A shared clock prevents different worker clocks from changing ownership.
    # clock_timestamp advances even inside a long-lived transaction.
    return await db.scalar(select(func.clock_timestamp()))


async def claim_next_agent_run(db: AsyncSession) -> str | None:
    now = await _lease_now(db)
    legacy_cutoff = now - timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS)
    other_run = aliased(AgentRun)
    await db.execute(
        update(AgentRun)
        .where(
            AgentRun.status == "running",
            AgentRun.lease_expires_at.is_(None),
            AgentRun.started_at < legacy_cutoff,
        )
        .values(
            status="failed",
            error_code="legacy_run_interrupted",
            completed_at=now,
        )
    )
    await db.execute(
        update(AgentRun)
        .where(
            AgentRun.status == "running",
            AgentRun.lease_expires_at < now,
            AgentRun.attempt_count >= settings.AGENT_RUN_MAX_ATTEMPTS,
        )
        .values(
            status="failed",
            error_code="worker_attempts_exhausted",
            completed_at=now,
            lease_expires_at=None,
        )
    )

    candidate = await db.scalar(
        select(AgentRun)
        .where(
            or_(
                AgentRun.status == "queued",
                (
                    (AgentRun.status == "running")
                    & (AgentRun.lease_expires_at < now)
                    & (AgentRun.attempt_count < settings.AGENT_RUN_MAX_ATTEMPTS)
                ),
            ),
            ~exists(
                select(other_run.id).where(
                    other_run.conversation_id == AgentRun.conversation_id,
                    other_run.user_id == AgentRun.user_id,
                    other_run.status.in_(['queued','running']),
                    precedes(other_run, AgentRun),
                )
            ),
            ~exists(
                select(other_run.id).where(
                    other_run.conversation_id == AgentRun.conversation_id,
                    other_run.id != AgentRun.id,
                    other_run.status == "running",
                    or_(
                        other_run.lease_expires_at.is_(None),
                        other_run.lease_expires_at > now,
                    ),
                )
            ),
        )
        .order_by(AgentRun.queued_at.asc(), AgentRun.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if candidate is None:
        await db.commit()
        return None

    candidate.status = "running"
    candidate.processing_started_at = candidate.processing_started_at or now
    candidate.lease_expires_at = now + timedelta(
        seconds=settings.AGENT_RUN_LEASE_SECONDS
    )
    candidate.attempt_count += 1
    candidate.error_code = None
    run_id = candidate.id
    attempt_count = candidate.attempt_count
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return None
    logger.info(
        "Agent run claimed: run_id=%s attempt=%s",
        run_id,
        attempt_count,
    )
    return run_id


async def renew_agent_run_lease(
    db: AsyncSession,
    *,
    run_id: str,
    expected_attempt_count: int,
) -> bool:
    """Extend a lease only while the same execution attempt still owns the run."""
    now = await _lease_now(db)
    result = await db.execute(
        update(AgentRun)
        .where(
            AgentRun.id == run_id,
            AgentRun.status == "running",
            AgentRun.attempt_count == expected_attempt_count,
        )
        .values(
            # A database clock correction or delayed heartbeat must never shorten
            # the stored deadline. Evaluate against the row atomically.
            lease_expires_at=func.greatest(
                AgentRun.lease_expires_at,
                now + timedelta(seconds=settings.AGENT_RUN_LEASE_SECONDS),
            )
        )
    )
    await db.commit()
    return result.rowcount == 1


async def _agent_run_lease_heartbeat(
    stop_event: asyncio.Event,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    expected_attempt_count: int,
) -> None:
    interval = max(
        0.1,
        min(30.0, settings.AGENT_RUN_LEASE_SECONDS / 3),
    )
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except TimeoutError:
            pass

        try:
            async with session_factory() as db:
                renewed = await renew_agent_run_lease(
                    db,
                    run_id=run_id,
                    expected_attempt_count=expected_attempt_count,
                )
            if not renewed:
                logger.warning(
                    "Agent run lease ownership lost: run_id=%s attempt=%s",
                    run_id,
                    expected_attempt_count,
                )
                return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Agent run lease renewal failed: run_id=%s attempt=%s",
                run_id,
                expected_attempt_count,
            )


async def process_agent_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: str,
) -> None:
    async with session_factory() as db:
        run = await db.get(AgentRun, run_id)
        if run is None or run.status != "running":
            return
        expected_attempt_count = run.attempt_count
        conversation = await db.get(AgentConversation, run.conversation_id)
        user_message = await db.scalar(
            select(AgentMessage).where(
                AgentMessage.run_id == run.id,
                AgentMessage.role == "user",
            )
        )
        if conversation is None or user_message is None:
            await db.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status == "running",
                    AgentRun.attempt_count == expected_attempt_count,
                )
                .values(
                    status="failed",
                    error_code="invalid_queued_run",
                    completed_at=datetime.now(timezone.utc),
                    lease_expires_at=None,
                )
            )
            await db.commit()
            return

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            _agent_run_lease_heartbeat(
                heartbeat_stop,
                session_factory,
                run_id=run.id,
                expected_attempt_count=expected_attempt_count,
            ),
            name=f"agent-run-heartbeat-{run.id}",
        )
        try:
            result = await execute_agent_run(
                db,
                run=run,
                conversation=conversation,
                user_message=user_message.content,
                artifact_action=(user_message.content_data or {}).get(
                    "artifact_action"
                ),
                clarification_action=(user_message.content_data or {}).get(
                    "clarification_action"
                ),
                expected_attempt_count=expected_attempt_count,
            )
            logger.info(
                "Agent async run completed: run_id=%s reply_chars=%s",
                run_id,
                len(result.reply),
            )
        except AgentRunOwnershipLost:
            logger.warning(
                "Stale Agent execution discarded: run_id=%s attempt=%s",
                run_id,
                expected_attempt_count,
            )
        except AIServiceError:
            logger.warning("Agent async run failed: run_id=%s", run_id)
        except Exception:
            logger.exception("Unexpected async Agent worker failure: run_id=%s", run_id)
            await db.rollback()
            await db.execute(
                update(AgentRun)
                .where(
                    AgentRun.id == run_id,
                    AgentRun.status == "running",
                    AgentRun.attempt_count == expected_attempt_count,
                )
                .values(
                    status="failed",
                    error_code="worker_runtime_error",
                    completed_at=datetime.now(timezone.utc),
                    lease_expires_at=None,
                )
            )
            await db.commit()
        finally:
            heartbeat_stop.set()
            await heartbeat_task


async def agent_worker_loop(
    stop_event: asyncio.Event,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
) -> None:
    logger.info("Agent durable worker started")
    try:
        while not stop_event.is_set():
            try:
                async with session_factory() as db:
                    run_id = await claim_next_agent_run(db)
                if run_id is not None:
                    await process_agent_run(session_factory, run_id)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Agent worker polling failed")

            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=settings.AGENT_WORKER_POLL_SECONDS,
                )
            except TimeoutError:
                pass
    finally:
        logger.info("Agent durable worker stopped")
