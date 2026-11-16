from __future__ import annotations

import json

from app.services.agent_tool_registry_read_authority_observation import (
    REGISTRY_READ_AUTHORITY_LOG_PREFIX,
    parse_registry_read_authority_line,
    registry_read_authority_gate_failures,
    summarize_registry_read_authority_lines,
)


def _line(
    run_id: str,
    *,
    mode: str = "enforce",
    reasons: list[str] | None = None,
    legacy: int = 1,
    effective: int = 1,
    denied: int = 0,
) -> str:
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "authority_mode": mode,
        "reason_codes": reasons or [],
        "legacy_tool_count": legacy,
        "effective_tool_count": effective,
        "denied_tool_count": denied,
    }
    return REGISTRY_READ_AUTHORITY_LOG_PREFIX + json.dumps(payload)


def test_parser_accepts_plain_and_json_enveloped_authority_lines():
    plain, plain_invalid = parse_registry_read_authority_line(_line("run-1"))
    envelope = json.dumps({"log": f"INFO: {_line('run-2')}"})
    wrapped, wrapped_invalid = parse_registry_read_authority_line(envelope)

    assert plain_invalid is wrapped_invalid is False
    assert plain is not None and plain["run_id"] == "run-1"
    assert wrapped is not None and wrapped["run_id"] == "run-2"


def test_parser_accepts_cloudbase_tsv_export_row():
    row = (
        "7\t2026-08-24 14:29:01\tINFO: "
        + _line("run-cloudbase")
        + "\tfitness-agent-api-014\tfitness-agent-api-014-pod"
    )

    record, invalid = parse_registry_read_authority_line(row)

    assert invalid is False
    assert record is not None and record["run_id"] == "run-cloudbase"


def test_parser_marks_malformed_prefixed_line_invalid():
    record, invalid = parse_registry_read_authority_line(
        REGISTRY_READ_AUTHORITY_LOG_PREFIX + "not-json"
    )

    assert record is None
    assert invalid is True


def test_strict_gate_accepts_exact_enforce_run_set():
    lines = [_line(f"run-{index}") for index in range(30)]
    summary = summarize_registry_read_authority_lines(lines)

    assert registry_read_authority_gate_failures(
        summary,
        min_enforced_runs=30,
        expected_run_ids=[f"run-{index}" for index in range(30)],
    ) == []


def test_strict_gate_rejects_fallback_denial_duplicates_and_shape_loss():
    lines = [
        _line("run-1"),
        _line("run-1"),
        _line(
            "run-2",
            mode="legacy_fallback",
            reasons=["registry_internal_error"],
            legacy=2,
            effective=1,
            denied=1,
        ),
        REGISTRY_READ_AUTHORITY_LOG_PREFIX + "{",
    ]
    summary = summarize_registry_read_authority_lines(lines)

    failures = registry_read_authority_gate_failures(
        summary,
        min_enforced_runs=3,
        expected_run_ids=["run-1", "run-2", "run-3"],
    )

    assert failures == [
        "invalid_authority_events",
        "duplicate_authority_run_ids",
        "insufficient_enforced_runs",
        "non_enforce_authority_mode",
        "authority_reason_codes_present",
        "authority_denied_tools_present",
        "authority_tool_count_mismatch",
        "missing_expected_authority_runs",
    ]
