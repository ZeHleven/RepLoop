from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.agent_trace import AgentObservationTrace, AgentExecutionTrace
from app.services.agent_intent import IntentResolution
from app.services.agent_tool_registry_shadow import (
    compare_registry_shadow_facts,
)
from app.services.agent_tool_registry_shadow_facts import (
    legacy_argument_schema_fact,
    registry_argument_schema_fact,
)
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
    attach_registry_shadow_report,
    create_registry_shadow_session,
    registry_shadow_is_sampled,
    registry_shadow_sample_bucket,
)
from app.services.agent_tools import READ_TOOL_IDS, build_read_tools
from app.services.agent_trace import build_initial_execution_trace


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


def _trace_with_observations() -> AgentExecutionTrace:
    trace = build_initial_execution_trace(
        _resolution(),
        [
            "profile.get_summary",
            "workout.get_active_session",
            "workout.get_next",
            "workout.list_history",
        ],
    )
    observations = [
        AgentObservationTrace(
            sequence=1,
            action_sequence=1,
            tool_id="workout.get_active_session",
            status="success",
            summary={"found": False},
            result_fingerprint="a" * 64,
        ),
        AgentObservationTrace(
            sequence=2,
            action_sequence=2,
            tool_id="workout.get_next",
            status="success",
            summary={"found": True, "plan_id": "private-plan-id"},
            result_fingerprint="b" * 64,
        ),
        AgentObservationTrace(
            sequence=3,
            action_sequence=3,
            tool_id="workout.list_history",
            status="success",
            summary={"count": 0},
            result_fingerprint="c" * 64,
        ),
    ]
    return trace.model_copy(update={"observations": observations})


def test_shadow_settings_default_to_fully_disabled():
    assert Settings.model_fields[
        "AGENT_TOOL_REGISTRY_SHADOW_ENABLED"
    ].default is False
    assert Settings.model_fields[
        "AGENT_TOOL_REGISTRY_SHADOW_SAMPLE_RATE"
    ].default == 0.0
    assert Settings.model_fields[
        "AGENT_TOOL_REGISTRY_SHADOW_PERSIST_TRACE"
    ].default is False
    assert Settings.model_fields[
        "AGENT_TOOL_REGISTRY_SHADOW_EMIT_METRICS"
    ].default is False


def test_stable_sampling_is_bounded_and_retry_independent():
    run_id = "shadow-stable-run"
    bucket = registry_shadow_sample_bucket(run_id)

    assert 0 <= bucket <= 9999
    assert bucket == int(
        hashlib.sha256(run_id.encode("utf-8")).hexdigest(),
        16,
    ) % 10_000
    assert registry_shadow_sample_bucket(run_id) == bucket
    assert registry_shadow_is_sampled(run_id, 0.0) is False
    assert registry_shadow_is_sampled(run_id, 1.0) is True
    assert create_registry_shadow_session(
        run_id=run_id,
        enabled=False,
        sample_rate=1.0,
    ) is None
    assert create_registry_shadow_session(
        run_id=run_id,
        enabled=True,
        sample_rate=0.0,
    ) is None


def test_sampling_failure_disables_shadow_without_escaping(monkeypatch):
    import app.services.agent_tool_registry_shadow_trace as shadow_trace

    def fail_bucket(_run_id):
        raise RuntimeError("sampling detail")

    monkeypatch.setattr(
        shadow_trace,
        "registry_shadow_sample_bucket",
        fail_bucket,
    )

    assert create_registry_shadow_session(
        run_id="sampling-failure",
        enabled=True,
        sample_rate=1.0,
    ) is None


def test_old_trace_remains_valid_and_shadow_requires_version_1_1():
    trace = build_initial_execution_trace(
        _resolution(),
        ["profile.get_summary"],
    )
    assert AgentExecutionTrace.model_validate(
        trace.model_dump(mode="json")
    ).trace_version == "1.0"
    assert "tool_registry_shadow" not in trace.model_dump(mode="json")

    session = ToolRegistryShadowSession(sample_bucket=12)
    session.record_route(_resolution(), ["profile.get_summary"])
    report = session.build_report()
    with pytest.raises(
        ValidationError,
        match="tool registry shadow requires trace version 1.1",
    ):
        AgentExecutionTrace.model_validate({
            **trace.model_dump(mode="json"),
            "tool_registry_shadow": report.model_dump(mode="json"),
        })


def test_trace_attachment_is_optional_and_marks_unreached_checks_skipped():
    trace = build_initial_execution_trace(
        _resolution(),
        ["profile.get_summary"],
    )
    session = ToolRegistryShadowSession(sample_bucket=42)
    session.record_route(_resolution(), ["profile.get_summary"])
    report = session.build_report()

    unchanged = attach_registry_shadow_report(
        trace,
        report,
        persist_trace=False,
    )
    attached = attach_registry_shadow_report(
        trace,
        report,
        persist_trace=True,
    )

    assert unchanged == trace
    assert unchanged.trace_version == "1.0"
    assert unchanged.tool_registry_shadow is None
    assert attached.trace_version == "1.1"
    assert attached.tool_registry_shadow is not None
    assert "tool_registry_shadow" in attached.model_dump(mode="json")
    assert attached.tool_registry_shadow.status == "mismatch"
    assert attached.tool_registry_shadow.checks[0].status == "mismatch"
    assert all(
        item.status == "skipped"
        for item in attached.tool_registry_shadow.checks[1:]
    )


def test_registry_argument_facts_are_independent_and_match_v1_schemas():
    tools = build_read_tools(
        object(),
        user_id="shadow-fact-builder",
        allowlist=list(READ_TOOL_IDS),
    )

    check = compare_registry_shadow_facts(
        "argument_schema",
        legacy_argument_schema_fact(tools),
        registry_argument_schema_fact(READ_TOOL_IDS),
    )

    assert check.status == "match"


def test_downstream_registry_facts_reuse_independent_registry_route():
    tools = build_read_tools(
        object(),
        user_id="shadow-independent-route",
        allowlist=["profile.get_summary"],
    )
    session = ToolRegistryShadowSession(sample_bucket=23)
    session.record_route(_resolution(), ["profile.get_summary"])
    session.record_constructed_tools(tools, ["profile.get_summary"])

    assert session.registry_routed_tool_ids == (
        "profile.get_summary",
        "workout.get_active_session",
        "workout.get_next",
        "workout.list_history",
    )
    assert session.checks["route_allowlist"].mismatch_codes == (
        "permission_expansion",
    )
    assert session.checks["constructed_tools"].mismatch_codes == (
        "registered_tool_missing",
    )


def test_complete_shadow_session_matches_without_storing_sensitive_summaries():
    tool_ids = [
        "profile.get_summary",
        "workout.get_active_session",
        "workout.get_next",
        "workout.list_history",
    ]
    tools = build_read_tools(
        object(),
        user_id="shadow-session",
        allowlist=tool_ids,
    )
    session = ToolRegistryShadowSession(sample_bucket=314)
    session.record_route(_resolution(), tool_ids)
    session.record_constructed_tools(tools, tool_ids)
    session.record_parallel_policy(tool_ids, plan_steps=[])
    session.record_final_observations(_trace_with_observations())

    report = session.build_report()
    serialized = json.dumps(report.model_dump(mode="json"), sort_keys=True)

    assert report.status == "match"
    assert len(report.checks) == 6
    assert all(item.status == "match" for item in report.checks)
    assert "private-plan-id" not in serialized
    assert "shadow-session" not in serialized


def test_fact_builder_failure_becomes_safe_shadow_error(monkeypatch):
    import app.services.agent_tool_registry_shadow_trace as shadow_trace

    def fail_builder(_tool_ids):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(
        shadow_trace,
        "legacy_route_allowlist_fact",
        fail_builder,
    )
    session = ToolRegistryShadowSession(sample_bucket=7)
    session.record_route(_resolution(), ["profile.get_summary"])

    check = session.checks["route_allowlist"]
    assert check.status == "error"
    assert check.mismatch_codes == ("shadow_internal_error",)
    assert check.error_category == "shadow_fact_builder_error"
    assert "sensitive internal detail" not in str(check.model_dump())


def test_registry_route_builder_failure_does_not_escape_shadow(monkeypatch):
    import app.services.agent_tool_registry_shadow_trace as shadow_trace

    def fail_builder(_resolution):
        raise RuntimeError("registry route detail")

    monkeypatch.setattr(
        shadow_trace,
        "registry_route_allowlist_fact",
        fail_builder,
    )
    session = ToolRegistryShadowSession(sample_bucket=8)

    session.record_route(_resolution(), ["profile.get_summary"])

    check = session.checks["route_allowlist"]
    assert check.status == "error"
    assert check.error_category == "shadow_fact_builder_error"
    assert check.legacy_fingerprint is None
    assert check.registry_fingerprint is None
    assert "registry route detail" not in str(check.model_dump())
