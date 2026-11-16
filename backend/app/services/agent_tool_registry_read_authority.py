"""Pure, settings-agnostic Tool Registry v2 read-authority selector."""

from __future__ import annotations

from collections.abc import Iterable

from app.schemas.agent_tool_registry import (
    ToolRegistryReadAuthorityDecision,
    ToolRegistryReadAuthorityEntryFact,
    ToolRegistryReadAuthorityReasonCode,
)


def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _append_unique(
    values: list[ToolRegistryReadAuthorityReasonCode],
    value: ToolRegistryReadAuthorityReasonCode,
) -> None:
    if value not in values:
        values.append(value)


def _legacy_fallback(
    legacy_tool_ids: tuple[str, ...],
) -> ToolRegistryReadAuthorityDecision:
    return ToolRegistryReadAuthorityDecision(
        authority_mode="legacy_fallback",
        effective_tool_ids=legacy_tool_ids,
        reason_codes=("registry_internal_error",),
    )


def select_registry_read_authority(
    *,
    legacy_tool_ids: Iterable[str],
    cohort_tool_ids: Iterable[str],
    registry_entries: Iterable[ToolRegistryReadAuthorityEntryFact] | None,
    registry_error: bool = False,
) -> ToolRegistryReadAuthorityDecision:
    """Select the stable intersection of legacy and Registry read authority.

    ``registry_entries`` contains Registry-derived candidates for the current
    route, not the complete static catalog. Catalog projection remains a
    separate step so unrelated registered tools are not treated as expansion.
    """

    stable_legacy_tool_ids = _stable_unique(legacy_tool_ids)
    if registry_error or registry_entries is None:
        return _legacy_fallback(stable_legacy_tool_ids)

    entries = tuple(registry_entries)
    if len(entries) != len({entry.tool_id for entry in entries}):
        return _legacy_fallback(stable_legacy_tool_ids)

    cohort = set(cohort_tool_ids)
    entries_by_id = {entry.tool_id: entry for entry in entries}
    effective_tool_ids: list[str] = []
    denied_tool_ids: list[str] = []
    reason_codes: list[ToolRegistryReadAuthorityReasonCode] = []

    for tool_id in stable_legacy_tool_ids:
        entry = entries_by_id.get(tool_id)
        if tool_id not in cohort:
            denied_tool_ids.append(tool_id)
            _append_unique(reason_codes, "outside_enforce_cohort")
            continue
        if entry is None:
            denied_tool_ids.append(tool_id)
            _append_unique(reason_codes, "unregistered_tool")
            continue
        if entry.availability != "active":
            denied_tool_ids.append(tool_id)
            _append_unique(reason_codes, "inactive_tool")
            continue
        if entry.mode != "read" or entry.side_effects != "none":
            denied_tool_ids.append(tool_id)
            if entry.mode != "read":
                _append_unique(reason_codes, "non_read_tool")
            if entry.side_effects != "none":
                _append_unique(reason_codes, "side_effecting_tool")
            continue
        effective_tool_ids.append(tool_id)

    legacy_set = set(stable_legacy_tool_ids)
    for entry in entries:
        registry_authorizes = (
            entry.tool_id in cohort
            and entry.availability == "active"
            and entry.mode == "read"
            and entry.side_effects == "none"
        )
        if registry_authorizes and entry.tool_id not in legacy_set:
            denied_tool_ids.append(entry.tool_id)
            _append_unique(reason_codes, "permission_expansion")

    return ToolRegistryReadAuthorityDecision(
        authority_mode="enforce",
        effective_tool_ids=effective_tool_ids,
        denied_tool_ids=_stable_unique(denied_tool_ids),
        reason_codes=reason_codes,
    )
