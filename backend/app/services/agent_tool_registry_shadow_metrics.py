"""Pure low-cardinality metric projection for Tool Registry shadow reports."""

from __future__ import annotations

from typing import cast, get_args

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowMetricErrorCategory,
    ToolRegistryShadowMetricSample,
    ToolRegistryShadowReport,
)


_ALLOWED_ERROR_CATEGORIES: frozenset[str] = frozenset(
    get_args(ToolRegistryShadowMetricErrorCategory)
)
_UNKNOWN_ERROR_CATEGORY: ToolRegistryShadowMetricErrorCategory = "other"


def _bounded_error_category(
    error_category: str | None,
) -> ToolRegistryShadowMetricErrorCategory:
    if error_category not in _ALLOWED_ERROR_CATEGORIES:
        return _UNKNOWN_ERROR_CATEGORY
    return cast(ToolRegistryShadowMetricErrorCategory, error_category)


def project_registry_shadow_metrics(
    report: ToolRegistryShadowReport,
) -> tuple[ToolRegistryShadowMetricSample, ...]:
    """Project one validated report without I/O or runtime side effects."""

    samples = [
        ToolRegistryShadowMetricSample(
            name="agent_tool_registry_shadow_runs_total",
            kind="counter",
            labels={"status": report.status},
            value=1,
        )
    ]

    for check in report.checks:
        samples.append(
            ToolRegistryShadowMetricSample(
                name="agent_tool_registry_shadow_checks_total",
                kind="counter",
                labels={
                    "check_type": check.check_type,
                    "status": check.status,
                },
                value=1,
            )
        )
        if check.status == "mismatch":
            for code in check.mismatch_codes:
                samples.append(
                    ToolRegistryShadowMetricSample(
                        name=(
                            "agent_tool_registry_shadow_mismatches_total"
                        ),
                        kind="counter",
                        labels={
                            "check_type": check.check_type,
                            "code": code,
                        },
                        value=1,
                    )
                )
        elif check.status == "error":
            samples.append(
                ToolRegistryShadowMetricSample(
                    name="agent_tool_registry_shadow_errors_total",
                    kind="counter",
                    labels={
                        "check_type": check.check_type,
                        "error_category": _bounded_error_category(
                            check.error_category
                        ),
                    },
                    value=1,
                )
            )

    if report.status != "not_sampled":
        samples.append(
            ToolRegistryShadowMetricSample(
                name="agent_tool_registry_shadow_latency_ms",
                kind="histogram",
                labels={},
                value=report.total_latency_ms,
            )
        )
    return tuple(samples)
