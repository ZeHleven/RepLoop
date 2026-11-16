from __future__ import annotations

import json
from pathlib import Path

from app.services.agent_tool_registry_shadow_observation import (
    parse_registry_shadow_metric_line,
    registry_shadow_observation_gate_failures,
    summarize_registry_shadow_metric_lines,
)
from scripts.summarize_registry_shadow_metrics import main


_LOG_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "agent_tool_registry_shadow_metric_log.txt"
)


def _fixture_lines() -> list[str]:
    return _LOG_FIXTURE.read_text(encoding="utf-8").splitlines()


def test_observation_summary_accepts_plain_and_json_envelope_logs():
    summary = summarize_registry_shadow_metric_lines(_fixture_lines())

    assert summary["input_line_count"] == 9
    assert summary["ignored_line_count"] == 1
    assert summary["invalid_metric_event_count"] == 0
    assert summary["metric_event_count"] == 8
    assert summary["sampled_run_count"] == 1
    assert summary["run_status_counts"] == {"match": 1}
    assert summary["run_match_rate"] == 1.0
    assert summary["check_event_count"] == 6
    assert set(summary["check_status_counts"]) == {
        "route_allowlist",
        "constructed_tools",
        "argument_schema",
        "parallel_policy",
        "conditional_evidence",
        "observation_semantics",
    }
    assert set(summary["check_match_rates"].values()) == {1.0}
    assert summary["mismatch_count"] == 0
    assert summary["error_count"] == 0
    assert summary["latency_ms"] == {
        "count": 1,
        "mean_ms": 3,
        "p50_ms": 3,
        "p95_ms": 3,
        "min_ms": 3,
        "max_ms": 3,
    }
    assert registry_shadow_observation_gate_failures(
        summary,
        min_sampled_runs=1,
        max_p95_latency_ms=5,
    ) == []


def test_parser_accepts_cloudbase_tsv_export_row():
    row = (
        "3\t2026-08-24 14:29:23\tINFO: agent_tool_registry_shadow_metric "
        '{"kind":"histogram","labels":{},'
        '"name":"agent_tool_registry_shadow_latency_ms","value":2}'
        "\tfitness-agent-api-014\tfitness-agent-api-014-pod"
    )

    sample, invalid = parse_registry_shadow_metric_line(row)

    assert invalid is False
    assert sample is not None
    assert sample.name == "agent_tool_registry_shadow_latency_ms"
    assert sample.value == 2


def test_observation_gate_detects_permission_expansion_and_shape_loss():
    lines = _fixture_lines() + [
        (
            "agent_tool_registry_shadow_metric "
            '{"name":"agent_tool_registry_shadow_checks_total",'
            '"kind":"counter","labels":{"check_type":"route_allowlist",'
            '"status":"mismatch"},"value":1}'
        ),
        (
            "agent_tool_registry_shadow_metric "
            '{"name":"agent_tool_registry_shadow_mismatches_total",'
            '"kind":"counter","labels":{"check_type":"route_allowlist",'
            '"code":"permission_expansion"},"value":1}'
        ),
    ]
    summary = summarize_registry_shadow_metric_lines(lines)

    assert registry_shadow_observation_gate_failures(
        summary,
        min_sampled_runs=1,
        max_p95_latency_ms=5,
    ) == [
        "permission_expansion_detected",
        "registry_mismatch_detected",
        "check_sample_count_mismatch",
    ]


def test_observation_gate_counts_invalid_events_and_fail_open_drops():
    summary = summarize_registry_shadow_metric_lines([
        "agent_tool_registry_shadow_metric private-invalid-payload",
        "Tool Registry shadow metric projection dropped",
        "Tool Registry shadow metric adapter dropped remaining samples",
    ])

    failures = registry_shadow_observation_gate_failures(
        summary,
        min_sampled_runs=1,
        max_p95_latency_ms=5,
    )

    assert summary["invalid_metric_event_count"] == 1
    assert summary["projection_drop_count"] == 1
    assert summary["adapter_drop_count"] == 1
    assert failures == [
        "insufficient_sampled_runs",
        "invalid_metric_events",
        "metric_projection_drops",
        "metric_adapter_drops",
    ]
    assert "private-invalid-payload" not in json.dumps(summary)


def test_observation_cli_enforces_explicit_sample_gate(capsys):
    exit_code = main([
        str(_LOG_FIXTURE),
        "--min-sampled-runs",
        "1",
        "--max-p95-latency-ms",
        "5",
        "--strict",
    ])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["gate"]["passed"] is True
    assert report["gate"]["failures"] == []


def test_observation_cli_fails_closed_on_insufficient_window(capsys):
    exit_code = main([str(_LOG_FIXTURE), "--strict"])

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert report["gate"]["passed"] is False
    assert report["gate"]["failures"] == ["insufficient_sampled_runs"]
