"""Independent attempt observations, never a transaction for business writes.

Pending intent/transport does not prove receipt or cost. Journal durability
failure stops execution before another side effect. Read outputs are JSON text
to preserve NUL/surrogates without invalid PostgreSQL JSONB strings.
"""
import asyncio
import json
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps

from sqlalchemy import select, update
from app.models.agent import AgentRun, AgentAttemptJournal

_CURRENT = ContextVar("agent_attempt_journal", default=None)

class AttemptAlreadyStarted(Exception):
    pass

class AttemptOwnershipLost(Exception):
    pass

class AttemptJournalUnavailable(RuntimeError):
    # SDKs may retry client entry, but the poisoned journal rejects every new
    # action before transport/tool execution. Ordinary Exception is required by
    # LangChain's error callbacks; a custom BaseException breaks their handling.
    pass

def assert_journal_available():
    journal = _CURRENT.get()
    if journal is not None and (journal.broken or not journal.active):
        raise AttemptJournalUnavailable("Attempt journal is unavailable")

def _json(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))

class AttemptJournal:
    def __init__(self, factory, run_id, attempt, journal_id):
        self.factory, self.run_id, self.attempt, self.id = factory, run_id, attempt, journal_id
        self.lock = asyncio.Lock()
        self.tools = []
        self.broken = False
        self.active = True

    async def save(self, *, ledger=None, new_action=False, finish=False):
        if self.broken or not self.active:
            raise AttemptJournalUnavailable("Attempt journal is unavailable")
        try:
            async with self.lock:
                async with self.factory() as db:
                    if new_action:
                        owner = (await db.execute(select(AgentRun.status, AgentRun.attempt_count).where(AgentRun.id == self.run_id))).one_or_none()
                        if owner != ("running", self.attempt):
                            raise AttemptOwnershipLost("Attempt no longer owns Run")
                    values = {"tool_calls": json.loads(_json(self.tools))}
                    if ledger is not None:
                        values["model_usage"] = ledger.snapshot(complete=True).model_dump(mode="json")
                    if finish:
                        values["finished_at"] = datetime.now(timezone.utc)
                    # This row is allocated before runtime takes any Run locks.
                    # Updating it never checks a new FK or commits the runtime DB.
                    result = await db.execute(update(AgentAttemptJournal).where(AgentAttemptJournal.id == self.id).values(**values))
                    if result.rowcount != 1:
                        raise AttemptJournalUnavailable("Attempt journal row is missing")
                    await db.commit()
        except AttemptOwnershipLost:
            raise
        except BaseException as exc:
            self.broken = True
            raise AttemptJournalUnavailable("Attempt observation could not be committed") from exc

    async def invoke(self, name, coroutine, args, kwargs):
        entry = {"sequence": len(self.tools)+1, "name": name, "state": "pending",
                 "arguments_json": _json({"args": args, "kwargs": kwargs}), "result_json": None,
                 "duration_ms": None, "error_type": None}
        self.tools.append(entry)
        await self.save(new_action=True)
        started = time.monotonic()
        try:
            result = await coroutine(*args, **kwargs)
            entry.update(state="completed", result_json=_json(result))
            return result
        except BaseException as exc:
            entry.update(state="failed" if isinstance(exc, Exception) else "cancelled", error_type=type(exc).__name__)
            raise
        finally:
            entry["duration_ms"] = max(0, round((time.monotonic()-started)*1000))
            await self.save()

def journal_read_tool(name, coroutine):
    @wraps(coroutine)
    async def invoke(*args, **kwargs):
        journal = _CURRENT.get()
        if journal is None:
            return await coroutine(*args, **kwargs)
        return await journal.invoke(name, coroutine, args, kwargs)
    return invoke

async def checkpoint_model(ledger, *, new_action=False):
    journal = _CURRENT.get()
    if journal is not None:
        await journal.save(ledger=ledger, new_action=new_action)

@asynccontextmanager
async def observe_attempt(factory, run_id, attempt):
    # Historical synchronous/test callers have no claimed worker generation.
    # They retain v2 semantics and must not appear as a durable worker attempt.
    if attempt < 1:
        yield None
        return
    async with factory() as db:
        run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
        if run is None or run.status != "running" or run.attempt_count != attempt:
            raise AttemptOwnershipLost("Attempt no longer owns Run")
        if await db.scalar(select(AgentAttemptJournal.id).where(AgentAttemptJournal.run_id == run_id, AgentAttemptJournal.worker_attempt == attempt)):
            raise AttemptAlreadyStarted("A worker already started this attempt")
        row = AgentAttemptJournal(run_id=run_id, worker_attempt=attempt, tool_calls=[])
        db.add(row)
        await db.commit()
        journal = AttemptJournal(factory, run_id, attempt, row.id)
    token = _CURRENT.set(journal)
    try:
        yield journal
        assert_journal_available()
    finally:
        try:
            if not journal.broken:
                await journal.save(finish=True)
        finally:
            journal.active = False
            _CURRENT.reset(token)

async def read_attempt_history(db, run):
    rows = (await db.scalars(select(AgentAttemptJournal).where(AgentAttemptJournal.run_id == run.id).order_by(AgentAttemptJournal.worker_attempt))).all()
    missing = sorted(set(range(1, run.attempt_count+1)) - {r.worker_attempt for r in rows})
    usages = [r.model_usage for r in rows if r.model_usage is not None]
    known_in = sum(u["known_input_tokens"] for u in usages)
    known_out = sum(u["known_output_tokens"] for u in usages)
    complete = run.status in {"completed", "failed"} and bool(rows) and not missing and len(usages) == len(rows) and all(
        u["observation_complete"] and not u["unclassified_client_errors"] and not u["transport_errors"]
        and u["usage_known_requests"] == u["http_requests"] for u in usages)
    return {
        "version": "attempt_journal_v1", "scope": "observed_worker_attempts",
        "run_status": run.status,
        "missing_attempts": missing, "usage_complete": complete,
        "known_input_tokens": known_in, "known_output_tokens": known_out,
        "input_tokens": known_in if complete else None, "output_tokens": known_out if complete else None,
        "attempts": [{"worker_attempt": r.worker_attempt, "started_at": r.started_at,
            "finished_at": r.finished_at,
            "state": "closed" if r.finished_at else ("active" if r.worker_attempt == run.attempt_count and run.status == "running" else "interrupted"),
            "model_usage": r.model_usage, "tool_calls": r.tool_calls} for r in rows],
    }
