"""Pure, privacy-bounded comparators for Tool Registry v2 shadow checks.

This module has no Agent runtime wiring. It performs no I/O and never receives
user messages, tool arguments, or observation payloads.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from app.schemas.agent_tool_registry import (
    ToolRegistryShadowCheck,
    ToolRegistryShadowCheckType,
    ToolRegistryShadowMismatchCode,
)
from app.services.agent_tool_registry import TOOL_REGISTRY_V2_BY_ID


ShadowFact = Mapping[str, Any]
ShadowComparator = Callable[[ShadowFact, ShadowFact], ToolRegistryShadowCheck]

_KNOWN_REGISTRY_TOOL_IDS = frozenset(TOOL_REGISTRY_V2_BY_ID)
_MISMATCH_CODE_ORDER: tuple[ToolRegistryShadowMismatchCode, ...] = (
    "permission_expansion",
    "registered_tool_missing",
    "unexpected_tool",
    "tool_order_mismatch",
    "langchain_name_mismatch",
    "argument_schema_mismatch",
    "default_argument_mismatch",
    "parallel_policy_mismatch",
    "conditional_evidence_mismatch",
    "observation_semantics_mismatch",
    "shadow_internal_error",
)
_OBSERVATION_CLASSIFICATIONS = frozenset(
    {"success_found", "success_missing", "success_empty", "error"}
)


def _normalize_json(value: Any, *, location: str = "fact") -> Any:
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{location} keys must be strings")
            normalized[key] = _normalize_json(
                item,
                location=f"{location}.{key}",
            )
        return normalized
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [
            _normalize_json(item, location=f"{location}[]")
            for item in value
        ]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{location} must contain only finite JSON values")


def _require_mapping(value: Any, *, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be an object")
    return value


def _require_exact_keys(
    value: dict[str, Any],
    expected: set[str],
    *,
    location: str,
) -> None:
    if set(value) != expected:
        raise ValueError(f"{location} contains unsupported fields")


def _require_allowed_keys(
    value: dict[str, Any],
    *,
    required: set[str],
    allowed: set[str],
    location: str,
) -> None:
    keys = set(value)
    if not required.issubset(keys) or not keys.issubset(allowed):
        raise ValueError(f"{location} contains unsupported fields")


def _require_list(value: Any, *, location: str, max_length: int = 8) -> list[Any]:
    if not isinstance(value, list) or len(value) > max_length:
        raise ValueError(f"{location} must be a bounded array")
    return value


def _require_string(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 120:
        raise ValueError(f"{location} must be a bounded string")
    return value


def _require_bool(value: Any, *, location: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{location} must be a boolean")
    return value


def _validate_tool_ids(value: Any, *, location: str) -> tuple[str, ...]:
    items = _require_list(value, location=location)
    tool_ids = tuple(
        _require_string(item, location=f"{location}[]") for item in items
    )
    if len(tool_ids) != len(set(tool_ids)):
        raise ValueError(f"{location} cannot contain duplicate tool ids")
    return tool_ids


def _validate_route_allowlist_fact(fact: dict[str, Any]) -> None:
    _require_exact_keys(fact, {"tool_ids"}, location="route_allowlist")
    _validate_tool_ids(fact["tool_ids"], location="route_allowlist.tool_ids")


def _validate_constructed_tools_fact(fact: dict[str, Any]) -> None:
    _require_exact_keys(fact, {"tools"}, location="constructed_tools")
    tools = _require_list(
        fact["tools"], location="constructed_tools.tools", max_length=30
    )
    tool_ids: list[str] = []
    for index, raw_tool in enumerate(tools):
        location = f"constructed_tools.tools[{index}]"
        tool = _require_mapping(raw_tool, location=location)
        _require_exact_keys(
            tool,
            {"tool_id", "langchain_name"},
            location=location,
        )
        tool_ids.append(_require_string(tool["tool_id"], location=location))
        _require_string(tool["langchain_name"], location=location)
    if len(tool_ids) != len(set(tool_ids)):
        raise ValueError("constructed_tools cannot contain duplicate tool ids")


def _validate_argument_schema_fact(fact: dict[str, Any]) -> None:
    _require_exact_keys(fact, {"tools"}, location="argument_schema")
    tools = _require_list(
        fact["tools"], location="argument_schema.tools", max_length=30
    )
    tool_ids: list[str] = []
    for tool_index, raw_tool in enumerate(tools):
        location = f"argument_schema.tools[{tool_index}]"
        tool = _require_mapping(raw_tool, location=location)
        _require_exact_keys(
            tool,
            {
                "tool_id",
                "schema_ref",
                "additional_properties",
                "fields",
            },
            location=location,
        )
        tool_ids.append(_require_string(tool["tool_id"], location=location))
        _require_string(tool["schema_ref"], location=location)
        _require_bool(tool["additional_properties"], location=location)
        fields = _require_list(
            tool["fields"],
            location=f"{location}.fields",
            max_length=30,
        )
        field_names: list[str] = []
        for field_index, raw_field in enumerate(fields):
            field_location = f"{location}.fields[{field_index}]"
            field = _require_mapping(raw_field, location=field_location)
            _require_allowed_keys(
                field,
                required={"name", "type", "required"},
                allowed={
                    "name",
                    "type",
                    "required",
                    "default",
                    "minimum",
                    "maximum",
                },
                location=field_location,
            )
            field_names.append(
                _require_string(field["name"], location=field_location)
            )
            _require_string(field["type"], location=field_location)
            _require_bool(field["required"], location=field_location)
            for bound in ("minimum", "maximum"):
                if bound in field and not isinstance(field[bound], (int, float)):
                    raise ValueError(f"{field_location}.{bound} must be numeric")
        if len(field_names) != len(set(field_names)):
            raise ValueError(f"{location} cannot contain duplicate fields")
    if len(tool_ids) != len(set(tool_ids)):
        raise ValueError("argument_schema cannot contain duplicate tool ids")


def _validate_parallel_policy_fact(fact: dict[str, Any]) -> None:
    _require_exact_keys(
        fact,
        {"tools", "conditional_pairs"},
        location="parallel_policy",
    )
    tools = _require_list(
        fact["tools"], location="parallel_policy.tools", max_length=30
    )
    tool_ids: list[str] = []
    for index, raw_tool in enumerate(tools):
        location = f"parallel_policy.tools[{index}]"
        tool = _require_mapping(raw_tool, location=location)
        _require_exact_keys(
            tool,
            {"tool_id", "mode", "side_effects", "parallel_safe"},
            location=location,
        )
        tool_ids.append(_require_string(tool["tool_id"], location=location))
        _require_string(tool["mode"], location=location)
        _require_string(tool["side_effects"], location=location)
        _require_bool(tool["parallel_safe"], location=location)
    if len(tool_ids) != len(set(tool_ids)):
        raise ValueError("parallel_policy cannot contain duplicate tool ids")

    pairs = _require_list(
        fact["conditional_pairs"],
        location="parallel_policy.conditional_pairs",
        max_length=30,
    )
    for index, raw_pair in enumerate(pairs):
        location = f"parallel_policy.conditional_pairs[{index}]"
        pair = _require_mapping(raw_pair, location=location)
        _require_exact_keys(
            pair,
            {
                "primary_tool_id",
                "fallback_tool_id",
                "speculative_parallel_allowed",
            },
            location=location,
        )
        _require_string(pair["primary_tool_id"], location=location)
        _require_string(pair["fallback_tool_id"], location=location)
        _require_bool(pair["speculative_parallel_allowed"], location=location)


def _validate_conditional_evidence_fact(fact: dict[str, Any]) -> None:
    _require_exact_keys(fact, {"events"}, location="conditional_evidence")
    events = _require_list(
        fact["events"],
        location="conditional_evidence.events",
        max_length=30,
    )
    for index, raw_event in enumerate(events):
        location = f"conditional_evidence.events[{index}]"
        event = _require_mapping(raw_event, location=location)
        _require_exact_keys(
            event,
            {"primary_tool_id", "primary_status", "fallback_tool_id"},
            location=location,
        )
        _require_string(event["primary_tool_id"], location=location)
        if event["primary_status"] not in _OBSERVATION_CLASSIFICATIONS:
            raise ValueError(f"{location}.primary_status is unsupported")
        if event["fallback_tool_id"] is not None:
            _require_string(event["fallback_tool_id"], location=location)


def _validate_observation_semantics_fact(fact: dict[str, Any]) -> None:
    _require_exact_keys(
        fact,
        {"observations"},
        location="observation_semantics",
    )
    observations = _require_list(
        fact["observations"],
        location="observation_semantics.observations",
        max_length=30,
    )
    for index, raw_observation in enumerate(observations):
        location = f"observation_semantics.observations[{index}]"
        observation = _require_mapping(raw_observation, location=location)
        _require_exact_keys(
            observation,
            {"tool_id", "run_status", "classification"},
            location=location,
        )
        _require_string(observation["tool_id"], location=location)
        if observation["run_status"] not in {"completed", "failed"}:
            raise ValueError(f"{location}.run_status is unsupported")
        if observation["classification"] not in _OBSERVATION_CLASSIFICATIONS:
            raise ValueError(f"{location}.classification is unsupported")


_FACT_VALIDATORS: dict[
    ToolRegistryShadowCheckType,
    Callable[[dict[str, Any]], None],
] = {
    "route_allowlist": _validate_route_allowlist_fact,
    "constructed_tools": _validate_constructed_tools_fact,
    "argument_schema": _validate_argument_schema_fact,
    "parallel_policy": _validate_parallel_policy_fact,
    "conditional_evidence": _validate_conditional_evidence_fact,
    "observation_semantics": _validate_observation_semantics_fact,
}


def _prepare_fact(
    check_type: ToolRegistryShadowCheckType,
    fact: ShadowFact,
) -> dict[str, Any]:
    normalized = _require_mapping(
        _normalize_json(fact),
        location=check_type,
    )
    _FACT_VALIDATORS[check_type](normalized)
    return normalized


def _fingerprint_prepared(fact: dict[str, Any]) -> str:
    canonical = json.dumps(
        fact,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def shadow_fact_fingerprint(
    check_type: ToolRegistryShadowCheckType,
    fact: ShadowFact,
) -> str:
    """Return a SHA-256 fingerprint only after privacy-shape validation."""

    return _fingerprint_prepared(_prepare_fact(check_type, fact))


def _ordered_codes(
    codes: set[ToolRegistryShadowMismatchCode],
) -> tuple[ToolRegistryShadowMismatchCode, ...]:
    return tuple(code for code in _MISMATCH_CODE_ORDER if code in codes)


def _tool_ids_from_prepared(
    check_type: ToolRegistryShadowCheckType,
    fact: dict[str, Any],
) -> tuple[str, ...]:
    if check_type == "route_allowlist":
        return tuple(fact["tool_ids"])
    if check_type in {
        "constructed_tools",
        "argument_schema",
        "parallel_policy",
    }:
        return tuple(item["tool_id"] for item in fact["tools"])

    ordered: list[str] = []
    items = (
        fact["events"]
        if check_type == "conditional_evidence"
        else fact["observations"]
    )
    for item in items:
        candidates = (
            (item["primary_tool_id"], item["fallback_tool_id"])
            if check_type == "conditional_evidence"
            else (item["tool_id"],)
        )
        for tool_id in candidates:
            if tool_id is not None and tool_id not in ordered:
                ordered.append(tool_id)
    return tuple(ordered)


def _build_check(
    *,
    check_type: ToolRegistryShadowCheckType,
    legacy_fact: dict[str, Any],
    registry_fact: dict[str, Any],
    mismatch_codes: set[ToolRegistryShadowMismatchCode],
) -> ToolRegistryShadowCheck:
    ordered_codes = _ordered_codes(mismatch_codes)
    return ToolRegistryShadowCheck(
        check_type=check_type,
        status="mismatch" if ordered_codes else "match",
        mismatch_codes=ordered_codes,
        legacy_fingerprint=_fingerprint_prepared(legacy_fact),
        registry_fingerprint=_fingerprint_prepared(registry_fact),
        legacy_tool_ids=_tool_ids_from_prepared(check_type, legacy_fact),
        registry_tool_ids=_tool_ids_from_prepared(check_type, registry_fact),
    )


def compare_route_allowlist(
    legacy_fact: ShadowFact,
    registry_fact: ShadowFact,
) -> ToolRegistryShadowCheck:
    legacy = _prepare_fact("route_allowlist", legacy_fact)
    registry = _prepare_fact("route_allowlist", registry_fact)
    legacy_ids = tuple(legacy["tool_ids"])
    registry_ids = tuple(registry["tool_ids"])
    legacy_set = set(legacy_ids)
    registry_set = set(registry_ids)
    codes: set[ToolRegistryShadowMismatchCode] = set()

    if registry_set - legacy_set:
        codes.add("permission_expansion")
    if legacy_set - registry_set:
        codes.add("registered_tool_missing")
    if legacy_set == registry_set and legacy_ids != registry_ids:
        codes.add("tool_order_mismatch")
    return _build_check(
        check_type="route_allowlist",
        legacy_fact=legacy,
        registry_fact=registry,
        mismatch_codes=codes,
    )


def compare_constructed_tools(
    legacy_fact: ShadowFact,
    registry_fact: ShadowFact,
) -> ToolRegistryShadowCheck:
    legacy = _prepare_fact("constructed_tools", legacy_fact)
    registry = _prepare_fact("constructed_tools", registry_fact)
    legacy_ids = tuple(item["tool_id"] for item in legacy["tools"])
    registry_ids = tuple(item["tool_id"] for item in registry["tools"])
    legacy_set = set(legacy_ids)
    registry_set = set(registry_ids)
    legacy_only = legacy_set - registry_set
    codes: set[ToolRegistryShadowMismatchCode] = set()

    if legacy_only - _KNOWN_REGISTRY_TOOL_IDS:
        codes.add("registered_tool_missing")
    if legacy_only & _KNOWN_REGISTRY_TOOL_IDS:
        codes.add("unexpected_tool")
    if registry_set - legacy_set:
        codes.add("registered_tool_missing")
    if legacy_set == registry_set and legacy_ids != registry_ids:
        codes.add("tool_order_mismatch")

    legacy_names = {
        item["tool_id"]: item["langchain_name"] for item in legacy["tools"]
    }
    registry_names = {
        item["tool_id"]: item["langchain_name"]
        for item in registry["tools"]
    }
    if any(
        legacy_names[tool_id] != registry_names[tool_id]
        for tool_id in legacy_set & registry_set
    ):
        codes.add("langchain_name_mismatch")
    return _build_check(
        check_type="constructed_tools",
        legacy_fact=legacy,
        registry_fact=registry,
        mismatch_codes=codes,
    )


def _without_defaults(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_defaults(item)
            for key, item in value.items()
            if key != "default"
        }
    if isinstance(value, list):
        return [_without_defaults(item) for item in value]
    return value


def _argument_defaults(
    fact: dict[str, Any],
) -> dict[tuple[str, str], tuple[bool, Any]]:
    return {
        (tool["tool_id"], field["name"]): (
            "default" in field,
            field.get("default"),
        )
        for tool in fact["tools"]
        for field in tool["fields"]
    }


def compare_argument_schema(
    legacy_fact: ShadowFact,
    registry_fact: ShadowFact,
) -> ToolRegistryShadowCheck:
    legacy = _prepare_fact("argument_schema", legacy_fact)
    registry = _prepare_fact("argument_schema", registry_fact)
    codes: set[ToolRegistryShadowMismatchCode] = set()
    if _without_defaults(legacy) != _without_defaults(registry):
        codes.add("argument_schema_mismatch")
    if _argument_defaults(legacy) != _argument_defaults(registry):
        codes.add("default_argument_mismatch")
    return _build_check(
        check_type="argument_schema",
        legacy_fact=legacy,
        registry_fact=registry,
        mismatch_codes=codes,
    )


def compare_parallel_policy(
    legacy_fact: ShadowFact,
    registry_fact: ShadowFact,
) -> ToolRegistryShadowCheck:
    legacy = _prepare_fact("parallel_policy", legacy_fact)
    registry = _prepare_fact("parallel_policy", registry_fact)
    codes: set[ToolRegistryShadowMismatchCode] = set()
    if legacy != registry:
        codes.add("parallel_policy_mismatch")
    return _build_check(
        check_type="parallel_policy",
        legacy_fact=legacy,
        registry_fact=registry,
        mismatch_codes=codes,
    )


def compare_conditional_evidence(
    legacy_fact: ShadowFact,
    registry_fact: ShadowFact,
) -> ToolRegistryShadowCheck:
    legacy = _prepare_fact("conditional_evidence", legacy_fact)
    registry = _prepare_fact("conditional_evidence", registry_fact)
    codes: set[ToolRegistryShadowMismatchCode] = set()
    if legacy != registry:
        codes.add("conditional_evidence_mismatch")
    return _build_check(
        check_type="conditional_evidence",
        legacy_fact=legacy,
        registry_fact=registry,
        mismatch_codes=codes,
    )


def compare_observation_semantics(
    legacy_fact: ShadowFact,
    registry_fact: ShadowFact,
) -> ToolRegistryShadowCheck:
    legacy = _prepare_fact("observation_semantics", legacy_fact)
    registry = _prepare_fact("observation_semantics", registry_fact)
    codes: set[ToolRegistryShadowMismatchCode] = set()
    if legacy != registry:
        codes.add("observation_semantics_mismatch")
    return _build_check(
        check_type="observation_semantics",
        legacy_fact=legacy,
        registry_fact=registry,
        mismatch_codes=codes,
    )


_COMPARATORS: dict[ToolRegistryShadowCheckType, ShadowComparator] = {
    "route_allowlist": compare_route_allowlist,
    "constructed_tools": compare_constructed_tools,
    "argument_schema": compare_argument_schema,
    "parallel_policy": compare_parallel_policy,
    "conditional_evidence": compare_conditional_evidence,
    "observation_semantics": compare_observation_semantics,
}


def compare_registry_shadow_facts(
    check_type: ToolRegistryShadowCheckType,
    legacy_fact: ShadowFact,
    registry_fact: ShadowFact,
) -> ToolRegistryShadowCheck:
    """Safely dispatch one pure comparison without leaking invalid input."""

    try:
        comparator = _COMPARATORS[check_type]
        return comparator(legacy_fact, registry_fact)
    except (KeyError, TypeError, ValueError):
        error_category = "invalid_shadow_fact"
    except Exception:  # pragma: no cover - runtime safety boundary
        error_category = "comparator_internal_error"
    return ToolRegistryShadowCheck(
        check_type=cast(ToolRegistryShadowCheckType, check_type),
        status="error",
        mismatch_codes=("shadow_internal_error",),
        error_category=error_category,
    )
