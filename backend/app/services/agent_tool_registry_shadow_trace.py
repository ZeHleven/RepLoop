"""Run-local orchestration for optional Tool Registry shadow reports."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowCheck,
    ToolRegistryShadowCheckType,
    ToolRegistryShadowReport,
)
from app.schemas.agent_trace import AgentExecutionTrace
from app.services.agent_intent import IntentResolution
from app.services.agent_tool_registry import TOOL_REGISTRY_V2
from app.services.agent_tool_registry_shadow import (
    ShadowFact,
    compare_registry_shadow_facts,
)
from app.services.agent_tool_registry_shadow_facts import (
    legacy_argument_schema_fact,
    legacy_conditional_evidence_fact,
    legacy_constructed_tools_fact,
    legacy_observation_semantics_fact,
    legacy_parallel_policy_fact,
    legacy_route_allowlist_fact,
    registry_argument_schema_fact,
    registry_conditional_evidence_fact,
    registry_constructed_tools_fact,
    registry_observation_semantics_fact,
    registry_parallel_policy_fact,
    registry_route_allowlist_fact,
)


_CHECK_ORDER: tuple[ToolRegistryShadowCheckType, ...] = (
    "route_allowlist",
    "constructed_tools",
    "argument_schema",
    "parallel_policy",
    "conditional_evidence",
    "observation_semantics",
)


def registry_shadow_sample_bucket(run_id: str) -> int:
    digest = hashlib.sha256(run_id.encode("utf-8")).digest()
    return int.from_bytes(digest, byteorder="big") % 10_000


def registry_shadow_is_sampled(run_id: str, sample_rate: float) -> bool:
    bounded_rate = min(1.0, max(0.0, sample_rate))
    threshold = int(bounded_rate * 10_000)
    return registry_shadow_sample_bucket(run_id) < threshold


@dataclass
class ToolRegistryShadowSession:
    sample_bucket: int
    checks: dict[ToolRegistryShadowCheckType, ToolRegistryShadowCheck] = field(
        default_factory=dict
    )
    registry_routed_tool_ids: tuple[str, ...] = ()
    route_recorded: bool = False

    def _record(
        self,
        check_type: ToolRegistryShadowCheckType,
        legacy_builder: Callable[[], ShadowFact],
        registry_builder: Callable[[], ShadowFact],
    ) -> None:
        started = time.perf_counter()
        try:
            check = compare_registry_shadow_facts(
                check_type,
                legacy_builder(),
                registry_builder(),
            )
        except Exception:  # pragma: no cover - defensive runtime boundary
            check = ToolRegistryShadowCheck(
                check_type=check_type,
                status="error",
                mismatch_codes=("shadow_internal_error",),
                error_category="shadow_fact_builder_error",
            )
        latency_ms = max(
            0,
            round((time.perf_counter() - started) * 1000),
        )
        self.checks[check_type] = check.model_copy(update={
            "latency_ms": latency_ms,
        })

    def record_route(
        self,
        resolution: IntentResolution,
        legacy_tool_ids: Sequence[str],
    ) -> None:
        try:
            registry_fact = registry_route_allowlist_fact(resolution)
        except Exception:
            self.route_recorded = True
            self.registry_routed_tool_ids = ()
            self.checks["route_allowlist"] = ToolRegistryShadowCheck(
                check_type="route_allowlist",
                status="error",
                mismatch_codes=("shadow_internal_error",),
                error_category="shadow_fact_builder_error",
            )
            return
        self.registry_routed_tool_ids = tuple(registry_fact["tool_ids"])
        self.route_recorded = True
        self._record(
            "route_allowlist",
            lambda: legacy_route_allowlist_fact(legacy_tool_ids),
            lambda: registry_fact,
        )

    def record_constructed_tools(
        self,
        tools: Sequence[BaseTool],
        expected_tool_ids: Sequence[str],
    ) -> None:
        registry_tool_ids = (
            self.registry_routed_tool_ids
            if self.route_recorded
            else tuple(expected_tool_ids)
        )
        self._record(
            "constructed_tools",
            lambda: legacy_constructed_tools_fact(tools),
            lambda: registry_constructed_tools_fact(registry_tool_ids),
        )
        self._record(
            "argument_schema",
            lambda: legacy_argument_schema_fact(tools),
            lambda: registry_argument_schema_fact(registry_tool_ids),
        )

    def record_parallel_policy(
        self,
        tool_ids: Sequence[str],
        *,
        plan_steps: Sequence[Any],
    ) -> None:
        registry_tool_ids = (
            self.registry_routed_tool_ids
            if self.route_recorded
            else tuple(tool_ids)
        )
        self._record(
            "parallel_policy",
            lambda: legacy_parallel_policy_fact(
                tool_ids,
                plan_steps=plan_steps,
            ),
            lambda: registry_parallel_policy_fact(registry_tool_ids),
        )

    def record_final_observations(self, trace: AgentExecutionTrace) -> None:
        self._record(
            "conditional_evidence",
            lambda: legacy_conditional_evidence_fact(trace),
            lambda: registry_conditional_evidence_fact(trace),
        )
        self._record(
            "observation_semantics",
            lambda: legacy_observation_semantics_fact(trace),
            lambda: registry_observation_semantics_fact(trace),
        )

    def build_report(self) -> ToolRegistryShadowReport:
        ordered_checks = tuple(
            self.checks.get(check_type)
            or ToolRegistryShadowCheck(
                check_type=check_type,
                status="skipped",
                skip_reason="lifecycle_point_not_reached",
            )
            for check_type in _CHECK_ORDER
        )
        statuses = {check.status for check in ordered_checks}
        if "error" in statuses:
            report_status = "error"
        elif "mismatch" in statuses:
            report_status = "mismatch"
        elif "skipped" in statuses:
            report_status = "partial"
        else:
            report_status = "match"
        return ToolRegistryShadowReport(
            registry_version=TOOL_REGISTRY_V2.registry_version,
            status=report_status,
            sample_bucket=self.sample_bucket,
            checks=ordered_checks,
            total_latency_ms=sum(check.latency_ms for check in ordered_checks),
        )


def create_registry_shadow_session(
    *,
    run_id: str,
    enabled: bool,
    sample_rate: float,
) -> ToolRegistryShadowSession | None:
    if not enabled:
        return None
    try:
        sample_bucket = registry_shadow_sample_bucket(run_id)
        bounded_rate = min(1.0, max(0.0, sample_rate))
    except Exception:
        return None
    if sample_bucket >= int(bounded_rate * 10_000):
        return None
    return ToolRegistryShadowSession(sample_bucket=sample_bucket)


def attach_registry_shadow_report(
    trace: AgentExecutionTrace,
    report: ToolRegistryShadowReport | None,
    *,
    persist_trace: bool,
) -> AgentExecutionTrace:
    if report is None or not persist_trace:
        return trace
    try:
        return trace.model_copy(update={
            "trace_version": (
                "1.2" if trace.trace_version == "1.2" else "1.1"
            ),
            "tool_registry_shadow": report,
        })
    except Exception:  # pragma: no cover - report loss must not fail v1
        return trace
