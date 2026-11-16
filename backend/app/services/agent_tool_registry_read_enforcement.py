"""Optional runtime adapter for Registry v2 read-only enforcement."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass

from app.schemas.agent_tool_registry import (
    ToolRegistryReadAuthorityDecision,
    ToolRegistryReadAuthorityEntryFact,
)
from app.services.agent_intent import IntentResolution
from app.services.agent_tool_registry import (
    TOOL_REGISTRY_V2_BY_ID,
    TOOL_REGISTRY_V2_READ_ENFORCEMENT,
    route_registry_read_tool_ids,
)
from app.services.agent_tool_registry_read_authority import (
    select_registry_read_authority,
)


# Uvicorn owns the production console handler. Route authority decisions through
# its error logger so INFO events remain visible in CloudBase container logs.
logger = logging.getLogger("uvicorn.error")

REGISTRY_READ_AUTHORITY_LOG_PREFIX = "agent_tool_registry_read_authority "


@dataclass(frozen=True)
class OptionalRegistryReadEnforcementResult:
    tool_allowlist: tuple[str, ...]
    decision: ToolRegistryReadAuthorityDecision | None = None


def _stable_unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _legacy_fallback_decision(
    legacy_tool_ids: tuple[str, ...],
) -> ToolRegistryReadAuthorityDecision:
    return ToolRegistryReadAuthorityDecision(
        authority_mode="legacy_fallback",
        effective_tool_ids=_stable_unique(legacy_tool_ids),
        reason_codes=("registry_internal_error",),
    )


def _candidate_facts(
    resolution: IntentResolution,
) -> tuple[ToolRegistryReadAuthorityEntryFact, ...]:
    facts: list[ToolRegistryReadAuthorityEntryFact] = []
    for tool_id in route_registry_read_tool_ids(resolution):
        entry = TOOL_REGISTRY_V2_BY_ID[tool_id]
        facts.append(ToolRegistryReadAuthorityEntryFact(
            tool_id=entry.tool_id,
            availability=entry.availability,
            mode=entry.mode,
            side_effects=entry.side_effects,
        ))
    return tuple(facts)


def _emit_decision_log(
    decision: ToolRegistryReadAuthorityDecision,
    *,
    run_id: str | None,
    legacy_tool_count: int,
) -> None:
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "authority_mode": decision.authority_mode,
        "reason_codes": list(decision.reason_codes),
        "legacy_tool_count": legacy_tool_count,
        "effective_tool_count": len(decision.effective_tool_ids),
        "denied_tool_count": len(decision.denied_tool_ids),
    }
    level = logging.WARNING if decision.reason_codes else logging.INFO
    try:
        logger.log(
            level,
            "%s%s",
            REGISTRY_READ_AUTHORITY_LOG_PREFIX,
            json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    except Exception:  # pragma: no cover - diagnostics must not affect a run
        pass


def apply_optional_registry_read_enforcement(
    *,
    resolution: IntentResolution,
    legacy_tool_ids: Iterable[str],
    enabled: bool,
    run_id: str | None = None,
) -> OptionalRegistryReadEnforcementResult:
    """Apply Registry authority only when enabled; otherwise return v1."""

    original_legacy_tool_ids = tuple(legacy_tool_ids)
    if not enabled:
        return OptionalRegistryReadEnforcementResult(
            tool_allowlist=original_legacy_tool_ids,
        )

    stable_legacy_tool_ids = _stable_unique(original_legacy_tool_ids)
    try:
        decision = select_registry_read_authority(
            legacy_tool_ids=stable_legacy_tool_ids,
            cohort_tool_ids=(
                TOOL_REGISTRY_V2_READ_ENFORCEMENT.tool_ids
            ),
            registry_entries=_candidate_facts(resolution),
        )
    except Exception:
        decision = _legacy_fallback_decision(stable_legacy_tool_ids)

    _emit_decision_log(
        decision,
        run_id=run_id,
        legacy_tool_count=len(stable_legacy_tool_ids),
    )
    return OptionalRegistryReadEnforcementResult(
        tool_allowlist=decision.effective_tool_ids,
        decision=decision,
    )
