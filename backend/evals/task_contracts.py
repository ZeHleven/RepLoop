"""Versioned user-task acceptance, independent of pytest/process completion."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, model_validator

DIMENSIONS = ("semantic", "evidence", "facts", "task_state", "persistence", "presentation", "latency")
DATASET = Path(__file__).with_name("agent_task_cases.json")


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: Literal["semantic", "evidence", "facts", "task_state", "persistence", "presentation", "latency"]
    path: str
    op: Literal["eq", "contains", "gte", "lte"] = "eq"
    expected: Any


class TaskCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    split: Literal["development", "holdout"]
    scenario: str
    message: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    live_supported: bool = False
    checks: list[Check] = Field(min_length=1)


class TaskDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0", "1.1"]
    description: str
    cases: list[TaskCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate task IDs")
        return self


def load_tasks() -> TaskDataset:
    return TaskDataset.model_validate_json(DATASET.read_text(encoding="utf-8"))


def score_task(case: TaskCase, record: dict | None) -> dict:
    """Missing observations, errors and skipped layers can never silently pass."""
    mode = record.get("model_mode") if record else None
    results = []
    for check in case.checks:
        status = "not_run"
        actual = None
        if record and not (check.dimension == "semantic" and mode != "live"):
            try:
                actual = record["observed"]
                for part in check.path.split("."):
                    actual = actual[part]
                if check.op == "eq":
                    numeric = (type(actual) in (int, float) and type(check.expected) in (int, float))
                    matches = (numeric or type(actual) is type(check.expected)) and actual == check.expected
                elif check.op == "contains":
                    matches = check.expected in actual
                elif check.op == "gte":
                    matches = not isinstance(actual, bool) and actual >= check.expected
                else:
                    matches = not isinstance(actual, bool) and actual <= check.expected
                status = "pass" if matches else "fail"
            except (KeyError, IndexError, TypeError, ValueError):
                actual = None
                status = "not_run"
        results.append({**check.model_dump(), "actual": actual, "status": status})
    dimensions = {}
    for dimension in DIMENSIONS:
        checks = [result["status"] for result in results if result["dimension"] == dimension]
        dimensions[dimension] = (
            "not_applicable" if not checks else "fail" if "fail" in checks
            else "not_run" if "not_run" in checks else "pass"
        )
    required = [item for item in results if mode == "live" or item["dimension"] != "semantic"]
    passed = mode in {"scripted", "live"} and bool(record and required) and all(item["status"] == "pass" for item in required)
    passed = passed and not record.get("error") if record else False
    return {"case_id": case.id, "split": case.split, "model_mode": mode,
            "passed": passed, "dimensions": dimensions, "checks": results,
            "error": record.get("error") if record else "missing_record"}


def build_report(cases: list[TaskCase], directory: Path, *, expected_model_mode: str | None = None) -> dict:
    scores = []
    for case in cases:
        path = directory / (case.id + ".json")
        record = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        record = recheck_raw_answers(record)
        if record and expected_model_mode and record.get("model_mode") != expected_model_mode:
            record["error"] = "model_mode_mismatch"
        scores.append(score_task(case, record))
    return {
        "schema_version": "1.0", "total": len(scores),
        "passed": sum(score["passed"] for score in scores),
        "failed": sum(not score["passed"] for score in scores),
        "dimensions": {dimension: dict(Counter(score["dimensions"][dimension] for score in scores))
                       for dimension in DIMENSIONS},
        "cases": scores,
    }


def recheck_raw_answers(record: dict | None) -> dict | None:
    """Recompute new answer gates from immutable raw responses during replay."""
    if record is None:
        return None
    from evals.nutrition_answer_checks import history_scope_claim_is_consistent, today_calories_appear
    record = deepcopy(record)
    for exchange in record.get("http", []):
        response = exchange.get("response", {})
        if not isinstance(response, dict) or not response.get("reply"):
            continue
        for card in response.get("cards", []):
            data = card.get("data", {})
            if card.get("type") == "nutrition.list_history":
                value = history_scope_claim_is_consistent(response["reply"], data)
            elif card.get("type") == "nutrition.get_today":
                value = today_calories_appear(response["reply"], data)
            else:
                continue
            record["observed"].setdefault("query", {})["reply_facts_consistent"] = value
    return record
