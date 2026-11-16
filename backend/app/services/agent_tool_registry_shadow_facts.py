"""Privacy-safe fact builders for Tool Registry v2 shadow comparison.

Legacy builders observe existing v1 structures. Registry builders derive from
the declarative Registry without calling tools or reading business data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool

from app.schemas.agent_trace import AgentExecutionTrace, AgentObservationTrace
from app.services.agent_intent import IntentResolution
from app.services.agent_tool_registry import (
    TOOL_REGISTRY_V2,
    TOOL_REGISTRY_V2_BY_ID,
    route_registry_read_tool_ids,
)
from app.services.agent_tools import (
    CONDITIONAL_READ_EVIDENCE_GROUPS,
    PARALLEL_READ_SAFE_TOOL_IDS,
    TOOL_ID_BY_LANGCHAIN_NAME,
)


def legacy_route_allowlist_fact(tool_ids: Sequence[str]) -> dict[str, Any]:
    return {"tool_ids": list(tool_ids)}


def registry_route_allowlist_fact(
    resolution: IntentResolution,
) -> dict[str, Any]:
    return {"tool_ids": list(route_registry_read_tool_ids(resolution))}


def _legacy_tool_id(tool: BaseTool) -> str:
    return TOOL_ID_BY_LANGCHAIN_NAME.get(tool.name, tool.name)


def legacy_constructed_tools_fact(
    tools: Sequence[BaseTool],
) -> dict[str, Any]:
    return {
        "tools": [
            {
                "tool_id": _legacy_tool_id(tool),
                "langchain_name": tool.name,
            }
            for tool in tools
        ]
    }


def registry_constructed_tools_fact(
    tool_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "tools": [
            {
                "tool_id": tool_id,
                "langchain_name": TOOL_REGISTRY_V2_BY_ID[
                    tool_id
                ].langchain_name,
            }
            for tool_id in tool_ids
            if tool_id in TOOL_REGISTRY_V2_BY_ID
        ]
    }


def _legacy_argument_fields(parameters: dict[str, Any]) -> list[dict[str, Any]]:
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = parameters.get("required")
    required_names = {
        item for item in required if isinstance(item, str)
    } if isinstance(required, list) else set()
    fields: list[dict[str, Any]] = []
    for name, raw_field in properties.items():
        if not isinstance(name, str) or not isinstance(raw_field, dict):
            continue
        field: dict[str, Any] = {
            "name": name,
            "type": str(raw_field.get("type") or "object"),
            "required": name in required_names,
        }
        for key in ("default", "minimum", "maximum"):
            if key in raw_field:
                field[key] = raw_field[key]
        fields.append(field)
    return fields


def legacy_argument_schema_fact(
    tools: Sequence[BaseTool],
) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    for tool in tools:
        args_schema = getattr(tool, "args_schema", None)
        parameters = (
            args_schema.model_json_schema()
            if args_schema is not None
            and hasattr(args_schema, "model_json_schema")
            else {}
        )
        facts.append({
            "tool_id": _legacy_tool_id(tool),
            "schema_ref": (
                args_schema.__name__ if args_schema is not None else "None"
            ),
            "additional_properties": bool(
                parameters.get("additionalProperties", True)
            ),
            "fields": _legacy_argument_fields(parameters),
        })
    return {"tools": facts}


def registry_argument_schema_fact(
    tool_ids: Sequence[str],
) -> dict[str, Any]:
    facts: list[dict[str, Any]] = []
    for tool_id in tool_ids:
        entry = TOOL_REGISTRY_V2_BY_ID.get(tool_id)
        if entry is None:
            continue
        fields: list[dict[str, Any]] = []
        for contract in entry.arguments.fields:
            field: dict[str, Any] = {
                "name": contract.name,
                "type": contract.json_type,
                "required": contract.required,
            }
            if contract.has_default:
                field["default"] = contract.default
            if contract.minimum is not None:
                field["minimum"] = contract.minimum
            if contract.maximum is not None:
                field["maximum"] = contract.maximum
            fields.append(field)
        facts.append({
            "tool_id": tool_id,
            "schema_ref": entry.arguments.schema_ref,
            "additional_properties": entry.arguments.additional_properties,
            "fields": fields,
        })
    return {"tools": facts}


def _prefetched_conditional_pairs(
    plan_steps: Sequence[Any],
) -> set[frozenset[str]]:
    prefetched: set[frozenset[str]] = set()
    for step in plan_steps:
        if getattr(step, "execution_strategy", None) != "parallel_read":
            continue
        action_ids = {
            item.tool_id
            for item in getattr(step, "planned_actions", ())
        }
        for group in CONDITIONAL_READ_EVIDENCE_GROUPS:
            pair = frozenset(
                {group.primary_tool_id, group.fallback_tool_id}
            )
            if pair.issubset(action_ids):
                prefetched.add(pair)
    return prefetched


def legacy_parallel_policy_fact(
    tool_ids: Sequence[str],
    *,
    plan_steps: Sequence[Any] = (),
) -> dict[str, Any]:
    selected = set(tool_ids)
    prefetched = _prefetched_conditional_pairs(plan_steps)
    return {
        "tools": [
            {
                "tool_id": tool_id,
                "mode": "read",
                "side_effects": "none",
                "parallel_safe": tool_id in PARALLEL_READ_SAFE_TOOL_IDS,
            }
            for tool_id in tool_ids
        ],
        "conditional_pairs": [
            {
                "primary_tool_id": group.primary_tool_id,
                "fallback_tool_id": group.fallback_tool_id,
                "speculative_parallel_allowed": frozenset({
                    group.primary_tool_id,
                    group.fallback_tool_id,
                }) in prefetched,
            }
            for group in CONDITIONAL_READ_EVIDENCE_GROUPS
            if {group.primary_tool_id, group.fallback_tool_id}.issubset(
                selected
            )
        ],
    }


def registry_parallel_policy_fact(
    tool_ids: Sequence[str],
) -> dict[str, Any]:
    selected = set(tool_ids)
    return {
        "tools": [
            {
                "tool_id": tool_id,
                "mode": entry.mode,
                "side_effects": entry.side_effects,
                "parallel_safe": entry.parallel_safe,
            }
            for tool_id in tool_ids
            if (entry := TOOL_REGISTRY_V2_BY_ID.get(tool_id)) is not None
        ],
        "conditional_pairs": [
            {
                "primary_tool_id": group.primary_tool_id,
                "fallback_tool_id": group.fallback_tool_id,
                "speculative_parallel_allowed": (
                    group.speculative_parallel_allowed
                ),
            }
            for group in TOOL_REGISTRY_V2.conditional_evidence
            if {group.primary_tool_id, group.fallback_tool_id}.issubset(
                selected
            )
        ],
    }


def legacy_observation_classification(
    observation: AgentObservationTrace,
) -> str:
    if observation.status == "error":
        return "error"
    summary = observation.summary
    if summary.get("found") is False:
        return "success_missing"
    if (
        observation.tool_id == "workout.list_history"
        and summary.get("count") == 0
    ):
        return "success_empty"
    if (
        observation.tool_id == "workout.get_progress"
        and summary.get("total_sessions") == 0
    ):
        return "success_empty"
    return "success_found"


def registry_observation_classification(
    observation: AgentObservationTrace,
) -> str:
    if observation.status == "error":
        return "error"
    entry = TOOL_REGISTRY_V2_BY_ID.get(observation.tool_id)
    if entry is None:
        return "success_found"
    signals = set(entry.observation.missing_data_signals)
    summary = observation.summary
    if "found_false" in signals and summary.get("found") is False:
        return "success_missing"
    if "count_zero" in signals and summary.get("count") == 0:
        return "success_empty"
    if (
        "empty_aggregate" in signals
        and summary.get("total_sessions") == 0
    ):
        return "success_empty"
    return "success_found"


def _observation_fact(
    observation: AgentObservationTrace,
    classification: str,
) -> dict[str, Any]:
    return {
        "tool_id": observation.tool_id,
        "run_status": (
            "completed" if observation.status == "success" else "failed"
        ),
        "classification": classification,
    }


def legacy_observation_semantics_fact(
    trace: AgentExecutionTrace,
) -> dict[str, Any]:
    return {
        "observations": [
            _observation_fact(
                observation,
                legacy_observation_classification(observation),
            )
            for observation in trace.observations
        ]
    }


def registry_observation_semantics_fact(
    trace: AgentExecutionTrace,
) -> dict[str, Any]:
    return {
        "observations": [
            _observation_fact(
                observation,
                registry_observation_classification(observation),
            )
            for observation in trace.observations
        ]
    }


def _observation_by_tool(
    trace: AgentExecutionTrace,
) -> dict[str, AgentObservationTrace]:
    return {
        observation.tool_id: observation
        for observation in sorted(trace.observations, key=lambda item: item.sequence)
    }


def legacy_conditional_evidence_fact(
    trace: AgentExecutionTrace,
) -> dict[str, Any]:
    observations = _observation_by_tool(trace)
    events: list[dict[str, Any]] = []
    for group in CONDITIONAL_READ_EVIDENCE_GROUPS:
        primary = observations.get(group.primary_tool_id)
        if primary is None:
            continue
        fallback = observations.get(group.fallback_tool_id)
        events.append({
            "primary_tool_id": group.primary_tool_id,
            "primary_status": legacy_observation_classification(primary),
            "fallback_tool_id": (
                group.fallback_tool_id
                if fallback is not None and fallback.sequence > primary.sequence
                else None
            ),
        })
    return {"events": events}


def registry_conditional_evidence_fact(
    trace: AgentExecutionTrace,
) -> dict[str, Any]:
    observations = _observation_by_tool(trace)
    events: list[dict[str, Any]] = []
    for group in TOOL_REGISTRY_V2.conditional_evidence:
        primary = observations.get(group.primary_tool_id)
        if primary is None:
            continue
        primary_status = registry_observation_classification(primary)
        fallback_required = (
            group.fallback_trigger == "on_error" and primary_status == "error"
        ) or (
            group.fallback_trigger == "on_not_found"
            and primary_status == "success_missing"
        )
        events.append({
            "primary_tool_id": group.primary_tool_id,
            "primary_status": primary_status,
            "fallback_tool_id": (
                group.fallback_tool_id if fallback_required else None
            ),
        })
    return {"events": events}
