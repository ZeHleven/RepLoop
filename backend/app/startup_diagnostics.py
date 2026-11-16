"""Privacy-safe startup identity and Agent feature diagnostics."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.config import settings


production_logger = logging.getLogger("uvicorn.error")

_BUILD_METADATA_PATH = Path(__file__).with_name("build_metadata.json")
_BUILD_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,39}$")
_BUILD_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")


@dataclass(frozen=True)
class BuildMetadata:
    version: str
    commit: str
    source_dirty: bool
    status: Literal["loaded", "missing", "invalid"]


def _fallback_build_metadata(
    status: Literal["missing", "invalid"],
) -> BuildMetadata:
    return BuildMetadata(
        version="development",
        commit="unknown",
        source_dirty=False,
        status=status,
    )


def load_build_metadata(path: Path | None = None) -> BuildMetadata:
    """Load the package-generated, non-secret build manifest fail-open."""

    metadata_path = path or _BUILD_METADATA_PATH
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _fallback_build_metadata("missing")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _fallback_build_metadata("invalid")

    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        return _fallback_build_metadata("invalid")
    version = payload.get("build_version")
    commit = payload.get("build_commit")
    source_dirty = payload.get("source_dirty")
    if (
        not isinstance(version, str)
        or _BUILD_VERSION_PATTERN.fullmatch(version) is None
        or not isinstance(commit, str)
        or _BUILD_COMMIT_PATTERN.fullmatch(commit) is None
        or not isinstance(source_dirty, bool)
    ):
        return _fallback_build_metadata("invalid")
    return BuildMetadata(
        version=version,
        commit=commit,
        source_dirty=source_dirty,
        status="loaded",
    )


def build_agent_startup_diagnostic(
    metadata: BuildMetadata,
    *,
    agent_enabled: bool,
    planned_execution_enabled: bool,
    plan_adjustment_proposals_enabled: bool,
    plan_management_proposals_enabled: bool = False,
    profile_proposals_enabled: bool = False,
    weight_proposals_enabled: bool = False,
    nutrition_proposals_enabled: bool = False,
) -> dict[str, str | bool]:
    """Project only explicitly approved, non-secret startup facts."""

    proposal_runtime_enabled = (
        agent_enabled
        and planned_execution_enabled
        and plan_adjustment_proposals_enabled
    )
    return {
        "schema_version": "1.0",
        "build_version": metadata.version,
        "build_commit": metadata.commit,
        "build_source_dirty": metadata.source_dirty,
        "build_metadata_status": metadata.status,
        "agent_enabled": bool(agent_enabled),
        "planned_execution_enabled": bool(planned_execution_enabled),
        "plan_adjustment_proposals_enabled": bool(
            plan_adjustment_proposals_enabled
        ),
        "plan_management_proposals_enabled": bool(
            plan_management_proposals_enabled
        ),
        "profile_proposals_enabled": bool(profile_proposals_enabled),
        "weight_proposals_enabled": bool(weight_proposals_enabled),
        "nutrition_proposals_enabled": bool(nutrition_proposals_enabled),
        "proposal_runtime_enabled": proposal_runtime_enabled,
        "intent_router_version": "semantic_route_v2",
        "rules_first_deprecated": True,
    }


def log_agent_startup_diagnostic(
    *,
    metadata_path: Path | None = None,
) -> dict[str, str | bool]:
    diagnostic = build_agent_startup_diagnostic(
        load_build_metadata(metadata_path),
        agent_enabled=settings.AGENT_ENABLED,
        planned_execution_enabled=settings.AGENT_PLANNED_EXECUTION_ENABLED,
        plan_adjustment_proposals_enabled=(
            settings.AGENT_PLAN_ADJUSTMENT_PROPOSALS_ENABLED
        ),
        plan_management_proposals_enabled=(
            settings.AGENT_PLAN_MANAGEMENT_PROPOSALS_ENABLED
        ),
        profile_proposals_enabled=settings.AGENT_PROFILE_PROPOSALS_ENABLED,
        weight_proposals_enabled=settings.AGENT_WEIGHT_PROPOSALS_ENABLED,
        nutrition_proposals_enabled=(
            settings.AGENT_NUTRITION_PROPOSALS_ENABLED
        ),
    )
    if settings.AGENT_RULES_FIRST_ENABLED:
        production_logger.warning(
            "AGENT_RULES_FIRST_ENABLED is deprecated and ignored for "
            "ordinary semantic requests"
        )
    production_logger.info(
        "agent_startup_diagnostic %s",
        json.dumps(
            diagnostic,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
    return diagnostic
