from __future__ import annotations

import json
import logging

from app.services.agent_intent import IntentResolution, route_tools
from app.services.agent_tool_registry_read_enforcement import (
    REGISTRY_READ_AUTHORITY_LOG_PREFIX,
    apply_optional_registry_read_enforcement,
    logger,
)


def _resolution(**overrides) -> IntentResolution:
    return IntentResolution.model_validate({
        "primary_intent": "profile_query",
        "evidence_requirements": ["profile_summary"],
        "resolved_query": "查询训练资料",
        "expanded_intents": [],
        "subtasks": ["查询训练资料"],
        "confidence": 0.9,
        **overrides,
    })


def _decision_logs(caplog) -> list[dict]:
    payloads: list[dict] = []
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith(REGISTRY_READ_AUTHORITY_LOG_PREFIX):
            payloads.append(json.loads(
                message[len(REGISTRY_READ_AUTHORITY_LOG_PREFIX):]
            ))
    return payloads


def test_authority_log_uses_production_console_logger():
    assert logger.name == "uvicorn.error"


def test_disabled_enforcement_returns_legacy_without_registry_projection(
    monkeypatch,
    caplog,
):
    def fail_projection(_resolution):
        raise AssertionError("disabled enforcement must not read Registry")

    monkeypatch.setattr(
        "app.services.agent_tool_registry_read_enforcement."
        "route_registry_read_tool_ids",
        fail_projection,
    )

    result = apply_optional_registry_read_enforcement(
        resolution=_resolution(),
        legacy_tool_ids=["profile.get_summary", "profile.get_summary"],
        enabled=False,
        run_id="disabled-run",
    )

    assert result.tool_allowlist == (
        "profile.get_summary",
        "profile.get_summary",
    )
    assert result.decision is None
    assert _decision_logs(caplog) == []


def test_enabled_enforcement_keeps_matching_read_authority(caplog):
    caplog.set_level(logging.INFO)

    result = apply_optional_registry_read_enforcement(
        resolution=_resolution(),
        legacy_tool_ids=["profile.get_summary"],
        enabled=True,
        run_id="matching-run",
    )

    assert result.tool_allowlist == ("profile.get_summary",)
    assert result.decision is not None
    assert result.decision.authority_mode == "enforce"
    assert result.decision.reason_codes == ()
    assert _decision_logs(caplog) == [{
        "authority_mode": "enforce",
        "denied_tool_count": 0,
        "effective_tool_count": 1,
        "legacy_tool_count": 1,
        "reason_codes": [],
        "run_id": "matching-run",
        "schema_version": "1.0",
    }]


def test_enabled_enforcement_matches_current_route_matrix():
    resolutions = [
        _resolution(primary_intent=intent)
        for intent in (
            "general_qa",
            "profile_query",
            "health_query",
            "plan_query",
            "next_workout_query",
            "active_workout_query",
            "workout_history_query",
            "workout_progress_query",
        )
    ]
    resolutions.extend((
        _resolution(
            primary_intent="active_workout_query",
            expanded_intents=["next_workout_query"],
        ),
        _resolution(
            primary_intent="plan_query",
            expanded_intents=[
                "workout_progress_query",
                "workout_history_query",
                "profile_query",
            ],
        ),
        _resolution(clarification_required=True),
        _resolution(risk_level="high"),
    ))

    for resolution in resolutions:
        legacy_tool_ids = route_tools(resolution)
        result = apply_optional_registry_read_enforcement(
            resolution=resolution,
            legacy_tool_ids=legacy_tool_ids,
            enabled=True,
        )

        assert result.tool_allowlist == tuple(legacy_tool_ids)
        assert result.decision is not None
        assert result.decision.authority_mode == "enforce"
        assert result.decision.denied_tool_ids == ()
        assert result.decision.reason_codes == ()


def test_enabled_enforcement_rejects_registry_permission_expansion(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        "app.services.agent_tool_registry_read_enforcement."
        "route_registry_read_tool_ids",
        lambda _resolution: (
            "profile.get_summary",
            "plan.get_active",
        ),
    )

    result = apply_optional_registry_read_enforcement(
        resolution=_resolution(),
        legacy_tool_ids=["profile.get_summary"],
        enabled=True,
        run_id="expansion-run",
    )

    assert result.tool_allowlist == ("profile.get_summary",)
    assert result.decision is not None
    assert result.decision.denied_tool_ids == ("plan.get_active",)
    assert result.decision.reason_codes == ("permission_expansion",)
    assert _decision_logs(caplog)[0]["denied_tool_count"] == 1


def test_registry_projection_error_falls_back_to_legacy_and_warns(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.WARNING)

    def fail_projection(_resolution):
        raise RuntimeError("private registry detail")

    monkeypatch.setattr(
        "app.services.agent_tool_registry_read_enforcement."
        "route_registry_read_tool_ids",
        fail_projection,
    )

    result = apply_optional_registry_read_enforcement(
        resolution=_resolution(),
        legacy_tool_ids=["plan.get_active", "profile.get_summary"],
        enabled=True,
        run_id="fallback-run",
    )

    assert result.tool_allowlist == (
        "plan.get_active",
        "profile.get_summary",
    )
    assert result.decision is not None
    assert result.decision.authority_mode == "legacy_fallback"
    assert result.decision.reason_codes == ("registry_internal_error",)
    logs = _decision_logs(caplog)
    assert logs[0]["authority_mode"] == "legacy_fallback"
    assert logs[0]["reason_codes"] == ["registry_internal_error"]
    assert "private registry detail" not in str(logs)


def test_selector_error_also_falls_back_without_calling_selector_twice(
    monkeypatch,
):
    calls = 0

    def fail_selector(**_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("selector detail")

    monkeypatch.setattr(
        "app.services.agent_tool_registry_read_enforcement."
        "select_registry_read_authority",
        fail_selector,
    )

    result = apply_optional_registry_read_enforcement(
        resolution=_resolution(),
        legacy_tool_ids=["profile.get_summary"],
        enabled=True,
        run_id="selector-fallback-run",
    )

    assert calls == 1
    assert result.tool_allowlist == ("profile.get_summary",)
    assert result.decision is not None
    assert result.decision.authority_mode == "legacy_fallback"
    assert result.decision.reason_codes == ("registry_internal_error",)


def test_high_risk_resolution_keeps_empty_authority_when_enabled():
    result = apply_optional_registry_read_enforcement(
        resolution=_resolution(risk_level="high"),
        legacy_tool_ids=[],
        enabled=True,
        run_id="safe-stop-run",
    )

    assert result.tool_allowlist == ()
    assert result.decision is not None
    assert result.decision.authority_mode == "enforce"
    assert result.decision.reason_codes == ()
