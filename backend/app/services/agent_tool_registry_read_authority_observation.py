"""Aggregate and gate privacy-safe Registry read-authority logs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


REGISTRY_READ_AUTHORITY_LOG_PREFIX = "agent_tool_registry_read_authority "
_LOG_MESSAGE_FIELDS = ("message", "msg", "text", "log")


def _line_candidates(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    candidates: list[str] = []
    try:
        envelope = json.loads(stripped)
    except (TypeError, ValueError):
        envelope = None
    if isinstance(envelope, dict):
        candidates.extend(
            value
            for field in _LOG_MESSAGE_FIELDS
            if isinstance((value := envelope.get(field)), str)
        )
    candidates.append(stripped)
    return tuple(dict.fromkeys(candidates))


def _non_negative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _normalize_payload(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("run_id")
    authority_mode = payload.get("authority_mode")
    reason_codes = payload.get("reason_codes")
    count_fields = (
        "legacy_tool_count",
        "effective_tool_count",
        "denied_tool_count",
    )
    if not isinstance(run_id, str) or not run_id:
        return None
    if authority_mode not in {"enforce", "legacy_fallback"}:
        return None
    if not isinstance(reason_codes, list) or not all(
        isinstance(item, str) and item for item in reason_codes
    ):
        return None
    if not all(_non_negative_int(payload.get(field)) for field in count_fields):
        return None
    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        return None
    return {
        "schema_version": schema_version,
        "run_id": run_id,
        "authority_mode": authority_mode,
        "reason_codes": tuple(reason_codes),
        "legacy_tool_count": payload["legacy_tool_count"],
        "effective_tool_count": payload["effective_tool_count"],
        "denied_tool_count": payload["denied_tool_count"],
    }


def parse_registry_read_authority_line(
    line: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Return a normalized record and whether an authority prefix was invalid."""

    prefix_seen = False
    for candidate in _line_candidates(line):
        if REGISTRY_READ_AUTHORITY_LOG_PREFIX not in candidate:
            continue
        prefix_seen = True
        payload_text = candidate.split(
            REGISTRY_READ_AUTHORITY_LOG_PREFIX,
            maxsplit=1,
        )[1].strip()
        try:
            payload, _ = json.JSONDecoder().raw_decode(payload_text)
        except (TypeError, ValueError):
            continue
        normalized = _normalize_payload(payload)
        if normalized is not None:
            return normalized, False
    return None, prefix_seen


def summarize_registry_read_authority_lines(
    lines: Iterable[str],
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    input_line_count = 0
    invalid_event_count = 0
    for line in lines:
        input_line_count += 1
        record, invalid = parse_registry_read_authority_line(line)
        if record is not None:
            records.append(record)
        elif invalid:
            invalid_event_count += 1

    mode_counts: dict[str, int] = {}
    reason_counts: dict[str, int] = {}
    run_counts: dict[str, int] = {}
    schema_version_counts: dict[str, int] = {}
    denied_record_count = 0
    count_mismatch_record_count = 0
    for record in records:
        mode = record["authority_mode"]
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
        version = record["schema_version"]
        schema_version_counts[version] = schema_version_counts.get(version, 0) + 1
        run_id = record["run_id"]
        run_counts[run_id] = run_counts.get(run_id, 0) + 1
        for reason in record["reason_codes"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if record["denied_tool_count"]:
            denied_record_count += 1
        if record["legacy_tool_count"] != record["effective_tool_count"]:
            count_mismatch_record_count += 1

    return {
        "input_line_count": input_line_count,
        "authority_event_count": len(records),
        "invalid_authority_event_count": invalid_event_count,
        "mode_counts": dict(sorted(mode_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "schema_version_counts": dict(sorted(schema_version_counts.items())),
        "unique_run_count": len(run_counts),
        "duplicate_run_ids": sorted(
            run_id for run_id, count in run_counts.items() if count != 1
        ),
        "denied_record_count": denied_record_count,
        "count_mismatch_record_count": count_mismatch_record_count,
        "run_ids": sorted(run_counts),
    }


def registry_read_authority_gate_failures(
    summary: dict[str, Any],
    *,
    min_enforced_runs: int,
    expected_run_ids: Iterable[str] | None = None,
) -> list[str]:
    failures: list[str] = []
    if summary["invalid_authority_event_count"]:
        failures.append("invalid_authority_events")
    if summary["duplicate_run_ids"]:
        failures.append("duplicate_authority_run_ids")
    if summary["mode_counts"].get("enforce", 0) < min_enforced_runs:
        failures.append("insufficient_enforced_runs")
    if set(summary["mode_counts"]) - {"enforce"}:
        failures.append("non_enforce_authority_mode")
    if summary["reason_counts"]:
        failures.append("authority_reason_codes_present")
    if summary["denied_record_count"]:
        failures.append("authority_denied_tools_present")
    if summary["count_mismatch_record_count"]:
        failures.append("authority_tool_count_mismatch")
    if set(summary["schema_version_counts"]) - {"1.0"}:
        failures.append("unexpected_authority_schema_version")

    if expected_run_ids is not None:
        expected = set(expected_run_ids)
        observed = set(summary["run_ids"])
        if expected - observed:
            failures.append("missing_expected_authority_runs")
        if observed - expected:
            failures.append("unexpected_authority_runs")
        if len(expected) < min_enforced_runs:
            failures.append("insufficient_expected_run_ids")
    return failures
