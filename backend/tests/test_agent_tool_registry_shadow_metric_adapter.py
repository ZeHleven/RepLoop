from __future__ import annotations

import logging
from collections.abc import Mapping
from unittest.mock import patch

from app.config import settings
from app.schemas.agent_tool_registry import (
    ToolRegistryShadowMetricName,
    ToolRegistryShadowReport,
)
from app.services.agent_intent import IntentResolution
from app.services.agent_runtime import _finalize_registry_shadow_trace
from app.services.agent_tool_registry_shadow_metric_adapter import (
    emit_registry_shadow_metrics,
    logger,
)
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
)
from app.services.agent_trace import build_initial_execution_trace


class RecordingMetricAdapter:
    def __init__(self) -> None:
        self.counters: list[dict[str, object]] = []
        self.histograms: list[dict[str, object]] = []

    def increment(
        self,
        name: ToolRegistryShadowMetricName,
        *,
        labels: Mapping[str, str],
        value: int,
    ) -> None:
        self.counters.append({
            "name": name,
            "labels": dict(labels),
            "value": value,
        })

    def observe(
        self,
        name: ToolRegistryShadowMetricName,
        *,
        labels: Mapping[str, str],
        value: int,
    ) -> None:
        self.histograms.append({
            "name": name,
            "labels": dict(labels),
            "value": value,
        })


class FailingMetricAdapter(RecordingMetricAdapter):
    def increment(
        self,
        name: ToolRegistryShadowMetricName,
        *,
        labels: Mapping[str, str],
        value: int,
    ) -> None:
        raise RuntimeError("private metric backend detail")


def test_default_adapter_uses_production_console_logger():
    assert logger.name == "uvicorn.error"


def _resolution() -> IntentResolution:
    return IntentResolution(
        primary_intent="profile_query",
        evidence_requirements=[
            "profile_summary",
            "active_workout_session",
            "next_workout",
            "workout_history",
        ],
        resolved_query="读取四类训练证据",
        expanded_intents=[
            "active_workout_query",
            "next_workout_query",
            "workout_history_query",
        ],
        subtasks=["读取四类训练证据"],
        confidence=0.9,
    )


def _report() -> ToolRegistryShadowReport:
    session = ToolRegistryShadowSession(sample_bucket=29)
    session.record_route(_resolution(), [
        "profile.get_summary",
        "workout.get_active_session",
        "workout.get_next",
        "workout.list_history",
    ])
    return session.build_report()


def test_metric_adapter_maps_projected_kinds_without_private_labels():
    report = _report()
    adapter = RecordingMetricAdapter()

    emitted = emit_registry_shadow_metrics(
        report,
        enabled=True,
        adapter=adapter,
    )

    assert emitted == 8
    assert len(adapter.counters) == 7
    assert adapter.histograms == [{
        "name": "agent_tool_registry_shadow_latency_ms",
        "labels": {},
        "value": report.total_latency_ms,
    }]
    labels = [
        item["labels"]
        for item in [*adapter.counters, *adapter.histograms]
    ]
    assert all(
        "registry_version" not in label and "sample_bucket" not in label
        for label in labels
    )


def test_default_adapter_emits_structured_privacy_safe_events(caplog):
    report = _report()

    with caplog.at_level(logging.INFO):
        emitted = emit_registry_shadow_metrics(report, enabled=True)

    events = [
        record.message
        for record in caplog.records
        if record.message.startswith("agent_tool_registry_shadow_metric ")
    ]
    assert emitted == 8
    assert len(events) == emitted
    assert all(
        '"name":"agent_tool_registry_shadow_' in item
        for item in events
    )
    assert "profile.get_summary" not in caplog.text
    assert report.registry_version not in caplog.text


def test_disabled_metric_emission_does_not_project(monkeypatch):
    import app.services.agent_tool_registry_shadow_metric_adapter as module

    def fail_projection(_report):
        raise AssertionError("disabled emission must not project")

    monkeypatch.setattr(
        module,
        "project_registry_shadow_metrics",
        fail_projection,
    )

    assert emit_registry_shadow_metrics(_report(), enabled=False) == 0


def test_projection_failure_is_fail_open_and_drops_metrics(
    monkeypatch,
    caplog,
):
    import app.services.agent_tool_registry_shadow_metric_adapter as module

    def fail_projection(_report):
        raise RuntimeError("private projection detail")

    monkeypatch.setattr(
        module,
        "project_registry_shadow_metrics",
        fail_projection,
    )

    with caplog.at_level(logging.WARNING):
        emitted = emit_registry_shadow_metrics(
            _report(),
            enabled=True,
            adapter=RecordingMetricAdapter(),
        )

    assert emitted == 0
    assert "projection dropped" in caplog.text
    assert "private projection detail" not in caplog.text


def test_adapter_failure_is_fail_open_and_redacts_backend_detail(caplog):
    with caplog.at_level(logging.WARNING):
        emitted = emit_registry_shadow_metrics(
            _report(),
            enabled=True,
            adapter=FailingMetricAdapter(),
        )

    assert emitted == 0
    assert "adapter dropped remaining samples" in caplog.text
    assert "private metric backend detail" not in caplog.text


def test_runtime_emits_without_trace_persistence():
    trace = build_initial_execution_trace(
        _resolution(),
        ["profile.get_summary"],
    )
    session = ToolRegistryShadowSession(sample_bucket=41)
    session.record_route(_resolution(), ["profile.get_summary"])

    with (
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS",
            True,
        ),
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE",
            False,
        ),
        patch(
            "app.services.agent_runtime.emit_registry_shadow_metrics"
        ) as emit,
    ):
        finalized = _finalize_registry_shadow_trace(trace, session)

    assert finalized == trace
    assert finalized.tool_registry_shadow is None
    emit.assert_called_once()
    report = emit.call_args.args[0]
    assert report.status == "mismatch"
    assert emit.call_args.kwargs == {"enabled": True}


def test_runtime_adapter_failure_does_not_drop_persisted_report():
    trace = build_initial_execution_trace(
        _resolution(),
        ["profile.get_summary"],
    )
    session = ToolRegistryShadowSession(sample_bucket=43)
    session.record_route(_resolution(), ["profile.get_summary"])

    with (
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS",
            True,
        ),
        patch.object(
            settings,
            "AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE",
            True,
        ),
        patch(
            "app.services.agent_runtime.emit_registry_shadow_metrics",
            side_effect=RuntimeError("unexpected adapter escape"),
        ),
    ):
        finalized = _finalize_registry_shadow_trace(trace, session)

    assert finalized.trace_version == "1.1"
    assert finalized.tool_registry_shadow is not None
