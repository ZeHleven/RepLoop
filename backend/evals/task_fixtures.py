"""Synthetic task runner fixtures. Imported explicitly, never application conftest."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.main import app
from app.models.agent import AgentRun, AgentToolCall
from evals.postgres_test_fixtures import db_session, engine, session_factory  # noqa: F401
from evals.task_contracts import load_tasks, score_task


def selected_tasks():
    selected = set(filter(None, os.environ.get("AGENT_TASK_EVAL_CASES", "").split(",")))
    return [case for case in load_tasks().cases if not selected or case.id in selected]


class TaskRecord:
    def __init__(self, case):
        self.case = case
        self.start = time.perf_counter()
        self.data = {"schema_version": "1.0", "case_id": case.id,
                     "model_mode": os.environ.get("AGENT_TASK_EVAL_MODE", "scripted"),
                     "data_source": "synthetic", "observed": {}, "http": [],
                     "raw_model_outputs": [], "runs": [], "tool_audits": []}

    def observe(self, group, **values):
        self.data["observed"].setdefault(group, {}).update(values)

    async def request(self, client, method, path, **kwargs):
        # Never save Authorization headers or auth endpoints/token responses.
        response = await client.request(method, path, **kwargs)
        self.data["http"].append({"method": method, "path": path,
                                 "request": kwargs.get("json"),
                                 "status": response.status_code, "response": response.json()})
        return response

    async def capture_runs(self, session, user_id):
        session.expire_all()
        runs = list((await session.scalars(select(AgentRun).where(AgentRun.user_id == user_id))).all())
        self.data["runs"] = [{"id": run.id, "request_kind": run.request_kind,
                             "status": run.status, "error_code": run.error_code,
                             "duration_ms": run.duration_ms, "input_tokens": run.input_tokens,
                             "output_tokens": run.output_tokens,
                             "evidence_requirements": run.evidence_requirements,
                             "execution_trace": run.execution_trace} for run in runs]
        self.data["usage_note"] = "Run token counters may omit structured subcalls; no aggregate cost claim."
        audits = list((await session.scalars(select(AgentToolCall).where(
            AgentToolCall.run_id.in_([run.id for run in runs])))).all())
        self.data["tool_audits"] = [
            {"run_id": item.run_id, "tool": item.tool_name, "status": item.status,
             "arguments": item.arguments_data, "result": item.result_data,
             "error_code": item.error_code} for item in audits]

    def finish(self):
        self.data["observed"]["duration_ms"] = round((time.perf_counter() - self.start) * 1000)
        score = score_task(self.case, self.data)
        directory = os.environ.get("AGENT_TASK_EVAL_DIR")
        if directory:
            Path(directory, self.case.id + ".json").write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return score


@pytest_asyncio.fixture
async def task_client(session_factory, monkeypatch):
    async def override():
        async with session_factory() as session:
            yield session

    # Every API dependency and independently opened read session uses the same
    # isolated schema. Never inherit a development application's DB session.
    monkeypatch.setattr("app.services.agent_runtime.AsyncSessionLocal", session_factory)
    monkeypatch.setattr(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_PLANNED_EXECUTION_ENABLED", False)
    app.dependency_overrides[get_db] = override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic-test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)
