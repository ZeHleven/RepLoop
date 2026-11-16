from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.schemas.agent_tool_registry import (
    ToolRegistryReadEnforcementContract,
    ToolRegistryV2,
)
from app.services.agent_intent import INTENT_TOOL_ALLOWLIST, MAX_ROUTED_TOOLS
from app.services.agent_tool_registry import (
    TOOL_REGISTRY_V2,
    TOOL_REGISTRY_V2_BY_ID,
    TOOL_REGISTRY_V2_INITIAL_READ_TOOL_IDS,
    TOOL_REGISTRY_V2_READ_ENFORCEMENT,
)
from app.services.agent_tools import (
    CONDITIONAL_READ_EVIDENCE_GROUPS,
    LANGCHAIN_TOOL_NAMES,
    PARALLEL_READ_SAFE_TOOL_IDS,
    READ_TOOL_IDS,
    TOOL_ID_BY_LANGCHAIN_NAME,
    build_read_tools,
)


def test_registry_v2_is_shadow_only_and_covers_every_active_read_tool():
    assert TOOL_REGISTRY_V2.status == "shadow"
    assert TOOL_REGISTRY_V2.max_routed_tools == MAX_ROUTED_TOOLS
    assert tuple(TOOL_REGISTRY_V2_BY_ID) == READ_TOOL_IDS
    assert set(TOOL_REGISTRY_V2_BY_ID) == set(PARALLEL_READ_SAFE_TOOL_IDS)
    assert all(item.availability == "active" for item in TOOL_REGISTRY_V2.tools)
    assert all(item.mode == "read" for item in TOOL_REGISTRY_V2.tools)
    assert all(item.side_effects == "none" for item in TOOL_REGISTRY_V2.tools)
    assert all(item.parallel_safe for item in TOOL_REGISTRY_V2.tools)


def test_registry_v2_matches_runtime_names_and_direct_intent_routes():
    assert {
        item.tool_id: item.langchain_name for item in TOOL_REGISTRY_V2.tools
    } == LANGCHAIN_TOOL_NAMES
    assert {
        item.langchain_name: item.tool_id for item in TOOL_REGISTRY_V2.tools
    } == TOOL_ID_BY_LANGCHAIN_NAME

    registry_by_intent = {
        intent: tuple(
            item.tool_id
            for item in TOOL_REGISTRY_V2.tools
            if intent in item.supported_intents
        )
        for intent in INTENT_TOOL_ALLOWLIST
    }
    assert registry_by_intent == INTENT_TOOL_ALLOWLIST


def test_registry_v2_argument_contracts_match_runtime_schemas(db_session):
    tools = build_read_tools(
        db_session,
        user_id="registry-audit-user",
        allowlist=list(READ_TOOL_IDS),
    )

    for tool in tools:
        tool_id = TOOL_ID_BY_LANGCHAIN_NAME[tool.name]
        contract = TOOL_REGISTRY_V2_BY_ID[tool_id].arguments
        assert tool.args_schema.__name__ == contract.schema_ref
        validated = tool.args_schema.model_validate(
            contract.default_arguments
        )
        assert validated.model_dump() == contract.default_arguments
        assert (
            tool.args_schema.model_json_schema()["additionalProperties"]
            is contract.additional_properties
        )
        assert "user_id" not in str(tool.args_schema.model_json_schema())


def test_registry_v2_matches_runtime_conditional_evidence_groups():
    runtime_groups = {
        (
            item.primary_tool_id,
            item.fallback_tool_id,
            item.fallback_trigger,
        )
        for item in CONDITIONAL_READ_EVIDENCE_GROUPS
    }
    registry_groups = {
        (
            item.primary_tool_id,
            item.fallback_tool_id,
            item.fallback_trigger,
        )
        for item in TOOL_REGISTRY_V2.conditional_evidence
    }

    assert registry_groups == runtime_groups
    assert all(
        item.fallback_arguments == {}
        and item.speculative_parallel_allowed is False
        for item in TOOL_REGISTRY_V2.conditional_evidence
    )


def test_registry_v2_records_current_output_and_audit_gaps():
    assert all(
        item.observation.current_shape == "legacy_mapping"
        and item.observation.strict_output_schema is False
        and item.observation.output_schema_ref is None
        for item in TOOL_REGISTRY_V2.tools
    )
    assert all(
        item.audit.result_storage == "summary_and_fingerprint"
        and item.audit.identity_logged is False
        for item in TOOL_REGISTRY_V2.tools
    )
    assert (
        TOOL_REGISTRY_V2_BY_ID[
            "health.get_screening_summary"
        ].data_sensitivity
        == "health_sensitive"
    )


def test_registry_v2_declares_run_local_freshness_without_enabling_it():
    assert all(
        item.freshness.reuse_scope == "run"
        and item.freshness.max_age_seconds > 0
        and item.freshness.invalidation_events
        for item in TOOL_REGISTRY_V2.tools
    )


def test_initial_registry_enforcement_is_disabled_and_read_only():
    contract = TOOL_REGISTRY_V2_READ_ENFORCEMENT

    assert Settings.model_fields[
        "AGENT_TOOL_REGISTRY_ENFORCE_READS_ENABLED"
    ].default is False
    assert contract.from_mode == "shadow"
    assert contract.to_mode == "enforce"
    assert contract.enabled_by_default is False
    assert contract.scope == "read_only"
    assert contract.tool_ids == TOOL_REGISTRY_V2_INITIAL_READ_TOOL_IDS
    assert contract.tool_ids == READ_TOOL_IDS
    assert all(
        TOOL_REGISTRY_V2_BY_ID[tool_id].availability == "active"
        and TOOL_REGISTRY_V2_BY_ID[tool_id].mode == "read"
        and TOOL_REGISTRY_V2_BY_ID[tool_id].side_effects == "none"
        for tool_id in contract.tool_ids
    )


def test_initial_registry_enforcement_is_intersection_only_and_reversible():
    contract = TOOL_REGISTRY_V2_READ_ENFORCEMENT

    assert contract.authority_surfaces == (
        "route_allowlist",
        "constructed_tools",
        "argument_schema",
        "parallel_policy",
        "conditional_evidence",
    )
    assert contract.effective_allowlist_policy == (
        "legacy_registry_intersection"
    )
    assert contract.fallback_on_registry_error == "legacy_read_runtime"
    assert contract.shadow_observation_during_enforce is True
    assert contract.rollback_mode == "legacy"
    assert contract.requires_data_migration is False


def test_registry_enforcement_contract_rejects_duplicate_scope_entries():
    payload = TOOL_REGISTRY_V2_READ_ENFORCEMENT.model_dump(mode="python")
    payload["tool_ids"] = [payload["tool_ids"][0]] * 2
    with pytest.raises(ValidationError, match="enforced tool ids must be unique"):
        ToolRegistryReadEnforcementContract.model_validate(payload)

    payload = TOOL_REGISTRY_V2_READ_ENFORCEMENT.model_dump(mode="python")
    payload["authority_surfaces"] = ["route_allowlist"] * 2
    with pytest.raises(ValidationError, match="authority surfaces must be unique"):
        ToolRegistryReadEnforcementContract.model_validate(payload)


def test_registry_v2_rejects_duplicate_tools_and_unknown_group_references():
    duplicate_payload = TOOL_REGISTRY_V2.model_dump(mode="python")
    duplicate_payload["tools"] = [
        *duplicate_payload["tools"],
        duplicate_payload["tools"][0],
    ]
    with pytest.raises(ValidationError, match="tool ids must be unique"):
        ToolRegistryV2.model_validate(duplicate_payload)

    unknown_reference_payload = TOOL_REGISTRY_V2.model_dump(mode="python")
    unknown_reference_payload["conditional_evidence"][0][
        "fallback_tool_id"
    ] = "workout.missing"
    with pytest.raises(
        ValidationError,
        match="must reference registered tools",
    ):
        ToolRegistryV2.model_validate(unknown_reference_payload)
