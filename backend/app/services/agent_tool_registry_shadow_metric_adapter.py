"""Fail-open adapter boundary for Tool Registry shadow metrics."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Protocol

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowMetricName,
    ToolRegistryShadowReport,
)
from app.services.agent_tool_registry_shadow_metrics import (
    project_registry_shadow_metrics,
)


# Uvicorn owns the production console handler. Using its error logger keeps
# INFO metric events visible in container logs without changing global logging.
logger = logging.getLogger("uvicorn.error")


class ToolRegistryShadowMetricAdapter(Protocol):
    """Minimal backend contract for counters and histograms."""

    def increment(
        self,
        name: ToolRegistryShadowMetricName,
        *,
        labels: Mapping[str, str],
        value: int,
    ) -> None: ...

    def observe(
        self,
        name: ToolRegistryShadowMetricName,
        *,
        labels: Mapping[str, str],
        value: int,
    ) -> None: ...


class StructuredLogToolRegistryShadowMetricAdapter:
    """Emit backend-neutral JSON events for log-based aggregation."""

    @staticmethod
    def _write(
        *,
        name: ToolRegistryShadowMetricName,
        kind: str,
        labels: Mapping[str, str],
        value: int,
    ) -> None:
        payload = {
            "name": name,
            "kind": kind,
            "labels": dict(labels),
            "value": value,
        }
        logger.info(
            "agent_tool_registry_shadow_metric %s",
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )

    def increment(
        self,
        name: ToolRegistryShadowMetricName,
        *,
        labels: Mapping[str, str],
        value: int,
    ) -> None:
        self._write(
            name=name,
            kind="counter",
            labels=labels,
            value=value,
        )

    def observe(
        self,
        name: ToolRegistryShadowMetricName,
        *,
        labels: Mapping[str, str],
        value: int,
    ) -> None:
        self._write(
            name=name,
            kind="histogram",
            labels=labels,
            value=value,
        )


_DEFAULT_ADAPTER = StructuredLogToolRegistryShadowMetricAdapter()


def emit_registry_shadow_metrics(
    report: ToolRegistryShadowReport,
    *,
    enabled: bool,
    adapter: ToolRegistryShadowMetricAdapter | None = None,
) -> int:
    """Best-effort projection and emission; failures never escape."""

    if not enabled:
        return 0
    try:
        samples = project_registry_shadow_metrics(report)
    except Exception:  # fail-open projection boundary
        logger.warning(
            "Tool Registry shadow metric projection dropped",
        )
        return 0

    target = adapter or _DEFAULT_ADAPTER
    emitted = 0
    for sample in samples:
        try:
            if sample.kind == "counter":
                target.increment(
                    sample.name,
                    labels=sample.labels,
                    value=sample.value,
                )
            else:
                target.observe(
                    sample.name,
                    labels=sample.labels,
                    value=sample.value,
                )
        except Exception:  # fail-open backend boundary
            logger.warning(
                "Tool Registry shadow metric adapter dropped remaining samples"
            )
            return emitted
        emitted += 1
    return emitted
