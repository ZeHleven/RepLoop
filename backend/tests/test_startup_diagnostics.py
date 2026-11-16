import json
import logging

from app.config import settings
from app.startup_diagnostics import (
    BuildMetadata,
    build_agent_startup_diagnostic,
    load_build_metadata,
    log_agent_startup_diagnostic,
)


def test_load_build_metadata_accepts_only_the_generated_contract(tmp_path):
    manifest = tmp_path / "build_metadata.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "build_version": "0.5.13-internal",
        "build_commit": "a" * 40,
        "source_dirty": False,
    }), encoding="utf-8")

    metadata = load_build_metadata(manifest)

    assert metadata == BuildMetadata(
        version="0.5.13-internal",
        commit="a" * 40,
        source_dirty=False,
        status="loaded",
    )


def test_load_build_metadata_fails_open_without_logging_untrusted_values(
    tmp_path,
):
    manifest = tmp_path / "build_metadata.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "build_version": "bad version\nSECRET_KEY=should-not-log",
        "build_commit": "not-a-commit",
        "source_dirty": "false",
    }), encoding="utf-8")

    invalid = load_build_metadata(manifest)
    missing = load_build_metadata(tmp_path / "missing.json")

    assert invalid == BuildMetadata(
        version="development",
        commit="unknown",
        source_dirty=False,
        status="invalid",
    )
    assert missing.status == "missing"


def test_startup_projection_distinguishes_flags_from_effective_runtime():
    metadata = BuildMetadata(
        version="0.5.13",
        commit="b" * 40,
        source_dirty=True,
        status="loaded",
    )

    diagnostic = build_agent_startup_diagnostic(
        metadata,
        agent_enabled=True,
        planned_execution_enabled=False,
        plan_adjustment_proposals_enabled=True,
        plan_management_proposals_enabled=True,
        profile_proposals_enabled=True,
        weight_proposals_enabled=False,
        nutrition_proposals_enabled=True,
    )

    assert diagnostic == {
        "schema_version": "1.0",
        "build_version": "0.5.13",
        "build_commit": "b" * 40,
        "build_source_dirty": True,
        "build_metadata_status": "loaded",
        "agent_enabled": True,
        "planned_execution_enabled": False,
        "plan_adjustment_proposals_enabled": True,
        "plan_management_proposals_enabled": True,
        "profile_proposals_enabled": True,
        "weight_proposals_enabled": False,
        "nutrition_proposals_enabled": True,
        "proposal_runtime_enabled": False,
        "intent_router_version": "semantic_route_v2",
        "rules_first_deprecated": True,
    }


def test_startup_log_contains_only_allowlisted_non_secret_fields(
    tmp_path,
    monkeypatch,
    caplog,
):
    manifest = tmp_path / "build_metadata.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "build_version": "0.5.13",
        "build_commit": "c" * 40,
        "source_dirty": False,
    }), encoding="utf-8")
    monkeypatch.setattr(settings, "AGENT_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_PLANNED_EXECUTION_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED",
        True,
    )
    monkeypatch.setattr(
        settings,
        "AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED",
        True,
    )
    monkeypatch.setattr(settings, "AGENT_PROFILE_PROPOSALS_ENABLED", True)
    monkeypatch.setattr(settings, "AGENT_WEIGHT_PROPOSALS_ENABLED", False)
    monkeypatch.setattr(settings, "AGENT_NUTRITION_PROPOSALS_ENABLED", True)
    monkeypatch.setattr(settings, "SECRET_KEY", "must-never-be-logged")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "also-never-logged")
    caplog.set_level(logging.INFO, logger="uvicorn.error")

    diagnostic = log_agent_startup_diagnostic(metadata_path=manifest)

    assert diagnostic["proposal_runtime_enabled"] is True
    assert "agent_startup_diagnostic" in caplog.text
    assert "must-never-be-logged" not in caplog.text
    assert "also-never-logged" not in caplog.text
    diagnostic_records = [
        record
        for record in caplog.records
        if "agent_startup_diagnostic" in record.message
    ]
    assert len(diagnostic_records) == 1
    assert diagnostic_records[0].name == "uvicorn.error"
    assert set(diagnostic) == {
        "schema_version",
        "build_version",
        "build_commit",
        "build_source_dirty",
        "build_metadata_status",
        "agent_enabled",
        "planned_execution_enabled",
        "plan_adjustment_proposals_enabled",
        "plan_management_proposals_enabled",
        "profile_proposals_enabled",
        "weight_proposals_enabled",
        "nutrition_proposals_enabled",
        "proposal_runtime_enabled",
        "intent_router_version",
        "rules_first_deprecated",
    }


def test_startup_warns_when_deprecated_rules_first_setting_is_enabled(
    tmp_path,
    monkeypatch,
    caplog,
):
    manifest = tmp_path / "build_metadata.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "build_version": "0.5.30",
        "build_commit": "d" * 40,
        "source_dirty": False,
    }), encoding="utf-8")
    monkeypatch.setattr(settings, "AGENT_RULES_FIRST_ENABLED", True)
    caplog.set_level(logging.WARNING, logger="uvicorn.error")

    log_agent_startup_diagnostic(metadata_path=manifest)

    assert "AGENT_RULES_FIRST_ENABLED is deprecated and ignored" in caplog.text
