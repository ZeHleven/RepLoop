"""Aggregate and gate privacy-safe Tool Registry shadow metric logs."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, get_args

from pydantic import ValidationError

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowCheckType,
    ToolRegistryShadowMetricSample,
)


REGISTRY_SHADOW_METRIC_LOG_PREFIX = "agent_tool_registry_shadow_metric "
REGISTRY_SHADOW_PROJECTION_DROP_LOG = (
    "Tool Registry shadow metric projection dropped"
)
REGISTRY_SHADOW_ADAPTER_DROP_LOG = (
    "Tool Registry shadow metric adapter dropped remaining samples"
)
_LOG_MESSAGE_FIELDS = ("message", "msg", "text", "log")
_REQUIRED_CHECK_TYPES = tuple(get_args(ToolRegistryShadowCheckType))


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


def parse_registry_shadow_metric_line(
    line: str,
) -> tuple[ToolRegistryShadowMetricSample | None, bool]:
    """Return a sample and whether a metric-prefixed line was invalid."""

    prefix_seen = False
    for candidate in _line_candidates(line):
        if REGISTRY_SHADOW_METRIC_LOG_PREFIX not in candidate:
            continue
        prefix_seen = True
        payload_text = candidate.split(
            REGISTRY_SHADOW_METRIC_LOG_PREFIX,
            maxsplit=1,
        )[1].strip()
        try:
            payload, _ = json.JSONDecoder().raw_decode(payload_text)
            return ToolRegistryShadowMetricSample.model_validate(payload), False
        except (TypeError, ValueError, ValidationError):
            continue
    return None, prefix_seen


def _increment_nested(
    target: dict[str, dict[str, int]],
    outer: str,
    inner: str,
    value: int,
) -> None:
    values = target.setdefault(outer, {})
    values[inner] = values.get(inner, 0) + value


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * min(1.0, max(0.0, quantile))
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    return round(
        ordered[lower_index]
        + (ordered[upper_index] - ordered[lower_index]) * fraction
    )


def _latency_summary(values: list[int]) -> dict[str, int]:
    if not values:
        return {
            "count": 0,
            "mean_ms": 0,
            "p50_ms": 0,
            "p95_ms": 0,
            "min_ms": 0,
            "max_ms": 0,
        }
    return {
        "count": len(values),
        "mean_ms": round(sum(values) / len(values)),
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def summarize_registry_shadow_metric_samples(
    samples: Iterable[ToolRegistryShadowMetricSample],
) -> dict[str, Any]:
    run_status_counts: dict[str, int] = {}
    check_status_counts: dict[str, dict[str, int]] = {}
    mismatch_counts: dict[str, dict[str, int]] = {}
    error_counts: dict[str, dict[str, int]] = {}
    latencies: list[int] = []
    event_count = 0

    for sample in samples:
        event_count += 1
        if sample.name == "agent_tool_registry_shadow_runs_total":
            status = sample.labels["status"]
            run_status_counts[status] = (
                run_status_counts.get(status, 0) + sample.value
            )
        elif sample.name == "agent_tool_registry_shadow_checks_total":
            _increment_nested(
                check_status_counts,
                sample.labels["check_type"],
                sample.labels["status"],
                sample.value,
            )
        elif sample.name == "agent_tool_registry_shadow_mismatches_total":
            _increment_nested(
                mismatch_counts,
                sample.labels["check_type"],
                sample.labels["code"],
                sample.value,
            )
        elif sample.name == "agent_tool_registry_shadow_errors_total":
            _increment_nested(
                error_counts,
                sample.labels["check_type"],
                sample.labels["error_category"],
                sample.value,
            )
        else:
            latencies.append(sample.value)

    sampled_run_count = sum(
        value
        for status, value in run_status_counts.items()
        if status != "not_sampled"
    )
    check_event_count = sum(
        value
        for statuses in check_status_counts.values()
        for value in statuses.values()
    )
    mismatch_count = sum(
        value
        for codes in mismatch_counts.values()
        for value in codes.values()
    )
    error_count = sum(
        value
        for categories in error_counts.values()
        for value in categories.values()
    )
    check_match_rates: dict[str, float | None] = {}
    for check_type in _REQUIRED_CHECK_TYPES:
        statuses = check_status_counts.get(check_type, {})
        evaluated = sum(
            statuses.get(status, 0)
            for status in ("match", "mismatch", "error")
        )
        check_match_rates[check_type] = (
            statuses.get("match", 0) / evaluated if evaluated else None
        )

    return {
        "metric_event_count": event_count,
        "sampled_run_count": sampled_run_count,
        "not_sampled_run_count": run_status_counts.get("not_sampled", 0),
        "run_status_counts": dict(sorted(run_status_counts.items())),
        "run_match_rate": (
            run_status_counts.get("match", 0) / sampled_run_count
            if sampled_run_count else 0.0
        ),
        "check_event_count": check_event_count,
        "check_status_counts": {
            check_type: dict(sorted(statuses.items()))
            for check_type, statuses in sorted(check_status_counts.items())
        },
        "check_match_rates": check_match_rates,
        "mismatching_check_count": sum(
            statuses.get("mismatch", 0)
            for statuses in check_status_counts.values()
        ),
        "mismatch_count": mismatch_count,
        "mismatch_counts": {
            check_type: dict(sorted(codes.items()))
            for check_type, codes in sorted(mismatch_counts.items())
        },
        "permission_expansion_count": sum(
            codes.get("permission_expansion", 0)
            for codes in mismatch_counts.values()
        ),
        "error_check_count": sum(
            statuses.get("error", 0)
            for statuses in check_status_counts.values()
        ),
        "error_count": error_count,
        "error_counts": {
            check_type: dict(sorted(categories.items()))
            for check_type, categories in sorted(error_counts.items())
        },
        "latency_ms": _latency_summary(latencies),
    }


def summarize_registry_shadow_metric_lines(
    lines: Iterable[str],
) -> dict[str, Any]:
    samples: list[ToolRegistryShadowMetricSample] = []
    input_line_count = 0
    invalid_metric_event_count = 0
    projection_drop_count = 0
    adapter_drop_count = 0

    for line in lines:
        input_line_count += 1
        if REGISTRY_SHADOW_PROJECTION_DROP_LOG in line:
            projection_drop_count += 1
        if REGISTRY_SHADOW_ADAPTER_DROP_LOG in line:
            adapter_drop_count += 1
        sample, invalid = parse_registry_shadow_metric_line(line)
        if sample is not None:
            samples.append(sample)
        elif invalid:
            invalid_metric_event_count += 1

    summary = summarize_registry_shadow_metric_samples(samples)
    summary.update({
        "input_line_count": input_line_count,
        "ignored_line_count": (
            input_line_count
            - len(samples)
            - invalid_metric_event_count
        ),
        "invalid_metric_event_count": invalid_metric_event_count,
        "projection_drop_count": projection_drop_count,
        "adapter_drop_count": adapter_drop_count,
    })
    return summary


def registry_shadow_observation_gate_failures(
    summary: dict[str, Any],
    *,
    min_sampled_runs: int,
    max_p95_latency_ms: int,
    min_run_match_rate: float | None = None,
    require_all_check_types: bool = True,
) -> list[str]:
    failures: list[str] = []
    sampled_run_count = int(summary["sampled_run_count"])
    if sampled_run_count < min_sampled_runs:
        failures.append("insufficient_sampled_runs")
    if summary["invalid_metric_event_count"]:
        failures.append("invalid_metric_events")
    if summary["projection_drop_count"]:
        failures.append("metric_projection_drops")
    if summary["adapter_drop_count"]:
        failures.append("metric_adapter_drops")
    if summary["permission_expansion_count"]:
        failures.append("permission_expansion_detected")
    if summary["mismatching_check_count"] or summary["mismatch_count"]:
        failures.append("registry_mismatch_detected")
    if summary["error_check_count"] or summary["error_count"]:
        failures.append("registry_shadow_error_detected")
    if summary["latency_ms"]["count"] != sampled_run_count:
        failures.append("latency_sample_count_mismatch")
    expected_check_events = sampled_run_count * len(_REQUIRED_CHECK_TYPES)
    if summary["check_event_count"] != expected_check_events:
        failures.append("check_sample_count_mismatch")
    if require_all_check_types and sampled_run_count:
        missing = sorted(
            set(_REQUIRED_CHECK_TYPES) - set(summary["check_status_counts"])
        )
        failures.extend(
            f"missing_check_type:{check_type}"
            for check_type in missing
        )
    if summary["latency_ms"]["p95_ms"] > max_p95_latency_ms:
        failures.append("shadow_latency_p95_exceeded")
    if (
        min_run_match_rate is not None
        and summary["run_match_rate"] < min_run_match_rate
    ):
        failures.append("run_match_rate_below_threshold")
    return failures
