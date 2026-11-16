from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path
from typing import Any

from app.schemas.agent_tool_registry import (
    ToolRegistryReadAuthorityEntryFact,
)
from app.services.agent_tool_registry_read_authority import (
    select_registry_read_authority,
)


_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_tool_registry_read_authority_cases.json"
)
_CASE_KEYS = {
    "case_id",
    "legacy_tool_ids",
    "cohort_tool_ids",
    "registry_entries",
    "registry_error",
    "expected",
}
_ENTRY_KEYS = {"tool_id", "availability", "mode", "side_effects"}
_EXPECTED_KEYS = {
    "authority_mode",
    "effective_tool_ids",
    "denied_tool_ids",
    "reason_codes",
}
_REASON_CODES = {
    "permission_expansion",
    "outside_enforce_cohort",
    "unregistered_tool",
    "inactive_tool",
    "non_read_tool",
    "side_effecting_tool",
    "registry_internal_error",
}
_FORBIDDEN_KEYS = {
    "user_id",
    "message",
    "prompt",
    "arguments",
    "raw_result",
    "result",
    "observation",
    "reply",
    "resource_id",
}


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    assert set(payload) == {"fixture_version", "cases"}
    assert payload["fixture_version"] == "1.0.0"
    return payload["cases"]


def _stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_nested_keys(item))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_nested_keys(item))
        return keys
    return set()


def _reference_expected(case: dict[str, Any]) -> dict[str, Any]:
    legacy_tool_ids = _stable_unique(case["legacy_tool_ids"])
    if case["registry_error"] is not None:
        return {
            "authority_mode": "legacy_fallback",
            "effective_tool_ids": legacy_tool_ids,
            "denied_tool_ids": [],
            "reason_codes": ["registry_internal_error"],
        }

    cohort_tool_ids = set(case["cohort_tool_ids"])
    entries = {
        entry["tool_id"]: entry for entry in case["registry_entries"]
    }
    effective_tool_ids: list[str] = []
    denied_tool_ids: list[str] = []
    reason_codes: list[str] = []

    for tool_id in legacy_tool_ids:
        entry = entries.get(tool_id)
        if tool_id not in cohort_tool_ids:
            denied_tool_ids.append(tool_id)
            _append_unique(reason_codes, "outside_enforce_cohort")
            continue
        if entry is None:
            denied_tool_ids.append(tool_id)
            _append_unique(reason_codes, "unregistered_tool")
            continue
        if entry["availability"] != "active":
            denied_tool_ids.append(tool_id)
            _append_unique(reason_codes, "inactive_tool")
            continue
        if entry["mode"] != "read" or entry["side_effects"] != "none":
            denied_tool_ids.append(tool_id)
            if entry["mode"] != "read":
                _append_unique(reason_codes, "non_read_tool")
            if entry["side_effects"] != "none":
                _append_unique(reason_codes, "side_effecting_tool")
            continue
        effective_tool_ids.append(tool_id)

    legacy_set = set(legacy_tool_ids)
    for entry in case["registry_entries"]:
        tool_id = entry["tool_id"]
        registry_authorizes = (
            tool_id in cohort_tool_ids
            and entry["availability"] == "active"
            and entry["mode"] == "read"
            and entry["side_effects"] == "none"
        )
        if registry_authorizes and tool_id not in legacy_set:
            denied_tool_ids.append(tool_id)
            _append_unique(reason_codes, "permission_expansion")

    return {
        "authority_mode": "enforce",
        "effective_tool_ids": effective_tool_ids,
        "denied_tool_ids": _stable_unique(denied_tool_ids),
        "reason_codes": reason_codes,
    }


def test_read_authority_case_fixtures_are_well_formed_and_privacy_safe():
    cases = _load_cases()

    assert len(cases) == 12
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert any(
        "recovery.get_summary" in case["cohort_tool_ids"]
        for case in cases
    )

    for case in cases:
        assert set(case) == _CASE_KEYS
        assert set(case["expected"]) == _EXPECTED_KEYS
        assert case["expected"]["authority_mode"] in {
            "enforce",
            "legacy_fallback",
        }
        assert case["registry_error"] in {None, "registry_internal_error"}
        assert len(case["cohort_tool_ids"]) == len(
            set(case["cohort_tool_ids"])
        )
        assert len(case["registry_entries"]) == len({
            entry["tool_id"] for entry in case["registry_entries"]
        })
        assert all(set(entry) == _ENTRY_KEYS for entry in case["registry_entries"])
        assert set(case["expected"]["reason_codes"]) <= _REASON_CODES
        assert _nested_keys(case).isdisjoint(_FORBIDDEN_KEYS)


def test_read_authority_cases_cover_each_stable_decision_reason():
    cases = _load_cases()

    assert {
        reason
        for case in cases
        for reason in case["expected"]["reason_codes"]
    } == _REASON_CODES
    assert {
        case["expected"]["authority_mode"] for case in cases
    } == {"enforce", "legacy_fallback"}
    assert any(not case["legacy_tool_ids"] for case in cases)
    assert any(
        len(case["legacy_tool_ids"])
        != len(set(case["legacy_tool_ids"]))
        for case in cases
    )


def test_read_authority_case_expectations_follow_the_intersection_contract():
    for case in _load_cases():
        assert case["expected"] == _reference_expected(case)


def test_read_authority_selector_matches_every_fixed_case():
    for case in _load_cases():
        entries = tuple(
            ToolRegistryReadAuthorityEntryFact.model_validate(entry)
            for entry in case["registry_entries"]
        )
        decision = select_registry_read_authority(
            legacy_tool_ids=case["legacy_tool_ids"],
            cohort_tool_ids=case["cohort_tool_ids"],
            registry_entries=entries,
            registry_error=case["registry_error"] is not None,
        )

        assert decision.model_dump(mode="json") == case["expected"]


def test_enforce_case_outputs_never_expand_legacy_or_cohort_authority():
    for case in _load_cases():
        expected = case["expected"]
        assert expected["effective_tool_ids"] == _stable_unique(
            expected["effective_tool_ids"]
        )
        assert expected["denied_tool_ids"] == _stable_unique(
            expected["denied_tool_ids"]
        )
        if expected["authority_mode"] != "enforce":
            continue

        effective = set(expected["effective_tool_ids"])
        assert effective <= set(case["legacy_tool_ids"])
        assert effective <= set(case["cohort_tool_ids"])
        entries = {
            entry["tool_id"]: entry for entry in case["registry_entries"]
        }
        assert all(
            entries[tool_id]["availability"] == "active"
            and entries[tool_id]["mode"] == "read"
            and entries[tool_id]["side_effects"] == "none"
            for tool_id in effective
        )


def test_registry_error_fixture_falls_back_to_stable_legacy_read_order():
    fallback_cases = [
        case
        for case in _load_cases()
        if case["expected"]["authority_mode"] == "legacy_fallback"
    ]

    assert len(fallback_cases) == 1
    case = fallback_cases[0]
    assert case["expected"]["effective_tool_ids"] == _stable_unique(
        case["legacy_tool_ids"]
    )
    assert case["expected"]["denied_tool_ids"] == []
    assert case["expected"]["reason_codes"] == [
        "registry_internal_error"
    ]


def test_duplicate_registry_facts_fail_safe_to_legacy_read_runtime():
    entry = ToolRegistryReadAuthorityEntryFact(
        tool_id="profile.get_summary",
        availability="active",
        mode="read",
        side_effects="none",
    )

    decision = select_registry_read_authority(
        legacy_tool_ids=["profile.get_summary"],
        cohort_tool_ids=["profile.get_summary"],
        registry_entries=[entry, entry],
    )

    assert decision.authority_mode == "legacy_fallback"
    assert decision.effective_tool_ids == ("profile.get_summary",)
    assert decision.denied_tool_ids == ()
    assert decision.reason_codes == ("registry_internal_error",)


def test_selector_intersection_invariant_for_all_small_authority_subsets():
    tool_ids = (
        "profile.get_summary",
        "plan.get_active",
        "recovery.get_summary",
    )
    entries = tuple(
        ToolRegistryReadAuthorityEntryFact(
            tool_id=tool_id,
            availability="active",
            mode="read",
            side_effects="none",
        )
        for tool_id in tool_ids
    )
    subsets = [
        subset
        for size in range(len(tool_ids) + 1)
        for subset in combinations(tool_ids, size)
    ]

    for legacy_tool_ids in subsets:
        for cohort_tool_ids in subsets:
            decision = select_registry_read_authority(
                legacy_tool_ids=legacy_tool_ids,
                cohort_tool_ids=cohort_tool_ids,
                registry_entries=entries,
            )

            assert decision.effective_tool_ids == tuple(
                tool_id
                for tool_id in legacy_tool_ids
                if tool_id in cohort_tool_ids
            )
            assert set(decision.effective_tool_ids) <= set(legacy_tool_ids)
            assert set(decision.effective_tool_ids) <= set(cohort_tool_ids)
