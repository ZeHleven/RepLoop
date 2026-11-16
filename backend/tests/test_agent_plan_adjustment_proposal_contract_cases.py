from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalPayload,
    plan_adjustment_proposal_payload_error_codes,
)
from app.services.agent_plan_adjustment_proposals import (
    evaluate_plan_adjustment_proposal_creation,
)


_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_contract_cases.json"
)
_TOP_LEVEL_KEYS = {
    "fixture_version",
    "canonical_payloads",
    "payload_cases",
    "creation_gate_cases",
    "transition_cases",
}
_PAYLOAD_CASE_KEYS = {
    "case_id",
    "base_payload",
    "mutations",
    "expected_error_codes",
}
_MUTATION_KEYS = {"op", "path", "value"}
_CREATION_CASE_KEYS = {
    "case_id",
    "feature_enabled",
    "run_owned",
    "selected_outcome",
    "terminal_action",
    "intent_allows_adjustment",
    "risk_level",
    "clarification_required",
    "evidence_state",
    "draft_state",
    "proposal_type",
    "requested_ttl_hours",
    "expected",
}
_CREATION_EXPECTED_KEYS = {
    "eligible",
    "reason_code",
    "initial_status",
    "ttl_hours",
}
_TRANSITION_CASE_KEYS = {
    "case_id",
    "current_status",
    "action",
    "owner_matches",
    "expected_version",
    "actual_version",
    "idempotency_replay",
    "time_state",
    "base_plan_state",
    "health_state",
    "candidate_state",
    "transaction_outcome",
    "expected",
}
_TRANSITION_EXPECTED_KEYS = {
    "result",
    "next_status",
    "error_code",
    "business_write_count",
}
_PROPOSAL_STATUSES = {
    "pending_confirmation",
    "applied",
    "rejected",
    "expired",
    "stale",
    "failed",
}
_TERMINAL_STATUSES = _PROPOSAL_STATUSES - {"pending_confirmation"}
_PAYLOAD_ERROR_CODES = {
    "payload_not_object",
    "forbidden_field",
    "missing_base_fingerprint",
    "incomplete_candidate_plan",
    "unsupported_change_type",
    "no_effect_change",
    "invalid_plan_bounds",
    "invalid_target",
}
_CREATION_GATE_REASON_CODES = {
    "feature_disabled",
    "run_ownership_lost",
    "health_red_flag",
    "clarification_required",
    "outcome_not_adjustment_proposal",
    "terminal_action_not_proposal",
    "intent_not_adjustment",
    "plan_evidence_missing",
    "supporting_evidence_missing",
    "deadline_evidence_insufficient",
    "proposal_draft_invalid",
    "proposal_target_ambiguous",
    "proposal_type_not_allowed",
    "proposal_ttl_out_of_range",
}
_TRANSITION_ERROR_CODES = {
    "proposal_not_found",
    "proposal_not_pending",
    "proposal_version_conflict",
    "proposal_expired",
    "proposal_base_plan_changed",
    "proposal_health_context_changed",
    "proposal_payload_invalid",
    "proposal_candidate_unavailable",
    "proposal_execution_failed",
}
_FORBIDDEN_PAYLOAD_KEYS = {
    "user_id",
    "is_active",
    "patch",
    "raw_prompt",
    "raw_result",
    "raw_observation",
    "message_history",
    "model_output",
    "jwt",
    "sql",
    "table_name",
}
_ALLOWED_CHANGE_TYPES = {
    "adjust_exercise_target",
    "replace_exercise",
    "update_plan_schedule",
}
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


def _load_fixture() -> dict[str, Any]:
    payload = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    assert set(payload) == _TOP_LEVEL_KEYS
    assert payload["fixture_version"] == "1.0.0"
    return payload


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        result = set(value)
        for item in value.values():
            result.update(_nested_keys(item))
        return result
    if isinstance(value, list):
        result: set[str] = set()
        for item in value:
            result.update(_nested_keys(item))
        return result
    return set()


def _resolve_parent(root: Any, path: str) -> tuple[Any, str]:
    segments = path.split(".")
    current = root
    for segment in segments[:-1]:
        current = (
            current[int(segment)]
            if isinstance(current, list)
            else current[segment]
        )
    return current, segments[-1]


def _apply_mutations(base: Any, mutations: list[dict[str, Any]]) -> Any:
    result = copy.deepcopy(base)
    for mutation in mutations:
        assert set(mutation) == _MUTATION_KEYS
        operation = mutation["op"]
        if operation == "replace_root":
            assert mutation["path"] == ""
            result = copy.deepcopy(mutation["value"])
            continue

        parent, key = _resolve_parent(result, mutation["path"])
        if isinstance(parent, list):
            index = int(key)
            if operation == "set":
                parent[index] = copy.deepcopy(mutation["value"])
            elif operation == "delete":
                del parent[index]
            else:  # pragma: no cover - fixture validation below owns this
                raise AssertionError(f"unknown mutation operation: {operation}")
        elif operation == "set":
            parent[key] = copy.deepcopy(mutation["value"])
        elif operation == "delete":
            del parent[key]
        else:  # pragma: no cover - fixture validation below owns this
            raise AssertionError(f"unknown mutation operation: {operation}")
    return result


def _reference_payload_errors(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return ["payload_not_object"]
    if _nested_keys(payload) & _FORBIDDEN_PAYLOAD_KEYS:
        return ["forbidden_field"]

    target = payload.get("target")
    if not isinstance(target, dict) or target.get("resource_type") != "workout_plan":
        return ["invalid_target"]
    fingerprint = target.get("base_plan_fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(
        fingerprint
    ):
        return ["missing_base_fingerprint"]

    after = payload.get("after")
    if not isinstance(after, dict) or not isinstance(after.get("exercises"), list):
        return ["incomplete_candidate_plan"]
    exercises = after["exercises"]
    if not exercises:
        return ["incomplete_candidate_plan"]
    if any(
        not isinstance(item, dict)
        or not 1 <= item.get("day_of_week", 0) <= 7
        or not 1 <= item.get("sets", 0) <= 8
        or not 15 <= item.get("rest_seconds", 0) <= 600
        for item in exercises
    ):
        return ["invalid_plan_bounds"]
    scheduled_days = {item["day_of_week"] for item in exercises}
    if len(scheduled_days) != after.get("days_per_week"):
        return ["invalid_plan_bounds"]

    changes = payload.get("changes")
    if not isinstance(changes, list) or not changes:
        return ["incomplete_candidate_plan"]
    if any(
        not isinstance(item, dict)
        or item.get("change_type") not in _ALLOWED_CHANGE_TYPES
        for item in changes
    ):
        return ["unsupported_change_type"]
    if any(item.get("before") == item.get("after") for item in changes):
        return ["no_effect_change"]

    required_top_level = {
        "schema_version",
        "proposal_type",
        "target",
        "before",
        "after",
        "changes",
        "evidence",
        "rationale",
        "safety_notes",
    }
    if set(payload) != required_top_level:
        return ["forbidden_field"]
    if payload["schema_version"] != "1.0.0":
        return ["forbidden_field"]
    if payload["proposal_type"] != "plan_adjustment_v1":
        return ["forbidden_field"]
    return []


def _reference_creation_gate(case: dict[str, Any]) -> dict[str, Any]:
    reason_code: str | None = None
    if not case["feature_enabled"]:
        reason_code = "feature_disabled"
    elif not case["run_owned"]:
        reason_code = "run_ownership_lost"
    elif case["risk_level"] == "high":
        reason_code = "health_red_flag"
    elif case["clarification_required"]:
        reason_code = "clarification_required"
    elif case["selected_outcome"] != "adjustment_proposal":
        reason_code = "outcome_not_adjustment_proposal"
    elif case["terminal_action"] != "proposal":
        reason_code = "terminal_action_not_proposal"
    elif not case["intent_allows_adjustment"]:
        reason_code = "intent_not_adjustment"
    elif case["evidence_state"] != "complete":
        reason_code = {
            "plan_missing": "plan_evidence_missing",
            "supporting_missing": "supporting_evidence_missing",
            "deadline_insufficient": "deadline_evidence_insufficient",
        }[case["evidence_state"]]
    elif case["draft_state"] != "valid":
        reason_code = {
            "invalid": "proposal_draft_invalid",
            "ambiguous_target": "proposal_target_ambiguous",
        }[case["draft_state"]]
    elif case["proposal_type"] != "plan_adjustment_v1":
        reason_code = "proposal_type_not_allowed"
    else:
        ttl_hours = case["requested_ttl_hours"]
        if ttl_hours is None:
            ttl_hours = 24
        if not 1 <= ttl_hours <= 72:
            reason_code = "proposal_ttl_out_of_range"
        else:
            return {
                "eligible": True,
                "reason_code": None,
                "initial_status": "pending_confirmation",
                "ttl_hours": ttl_hours,
            }

    return {
        "eligible": False,
        "reason_code": reason_code,
        "initial_status": None,
        "ttl_hours": None,
    }


def _transition_result(
    *,
    result: str,
    next_status: str | None,
    error_code: str | None,
    business_write_count: int = 0,
) -> dict[str, Any]:
    return {
        "result": result,
        "next_status": next_status,
        "error_code": error_code,
        "business_write_count": business_write_count,
    }


def _reference_transition(case: dict[str, Any]) -> dict[str, Any]:
    status = case["current_status"]
    action = case["action"]
    if not case["owner_matches"]:
        return _transition_result(
            result="not_found",
            next_status=None,
            error_code="proposal_not_found",
        )
    if case["idempotency_replay"] and (
        (status == "applied" and action == "confirm")
        or (status == "rejected" and action == "reject")
    ):
        return _transition_result(
            result="idempotent",
            next_status=status,
            error_code=None,
        )
    if status == "applied" and action == "confirm":
        return _transition_result(
            result="idempotent",
            next_status="applied",
            error_code=None,
        )
    if status != "pending_confirmation":
        return _transition_result(
            result="conflict",
            next_status=status,
            error_code="proposal_not_pending",
        )
    if case["expected_version"] != case["actual_version"]:
        return _transition_result(
            result="conflict",
            next_status=status,
            error_code="proposal_version_conflict",
        )
    if case["time_state"] == "at_or_after_expiry":
        return _transition_result(
            result="expired",
            next_status="expired",
            error_code="proposal_expired",
        )
    if action == "reject":
        return _transition_result(
            result="rejected",
            next_status="rejected",
            error_code=None,
        )
    if case["base_plan_state"] != "match_active":
        return _transition_result(
            result="stale",
            next_status="stale",
            error_code="proposal_base_plan_changed",
        )
    if case["health_state"] != "compatible":
        return _transition_result(
            result="stale",
            next_status="stale",
            error_code="proposal_health_context_changed",
        )
    if case["candidate_state"] == "unavailable":
        return _transition_result(
            result="stale",
            next_status="stale",
            error_code="proposal_candidate_unavailable",
        )
    if case["candidate_state"] == "invalid":
        return _transition_result(
            result="stale",
            next_status="stale",
            error_code="proposal_payload_invalid",
        )
    if case["transaction_outcome"] == "failure":
        return _transition_result(
            result="failed",
            next_status="failed",
            error_code="proposal_execution_failed",
        )
    return _transition_result(
        result="applied",
        next_status="applied",
        error_code=None,
        business_write_count=1,
    )


def test_proposal_contract_fixture_is_well_formed_and_bounded():
    fixture = _load_fixture()
    payload_cases = fixture["payload_cases"]
    creation_cases = fixture["creation_gate_cases"]
    transition_cases = fixture["transition_cases"]

    assert len(fixture["canonical_payloads"]) == 2
    assert len(payload_cases) == 12
    assert len(creation_cases) == 16
    assert len(transition_cases) == 16

    all_cases = [*payload_cases, *creation_cases, *transition_cases]
    assert len({case["case_id"] for case in all_cases}) == len(all_cases)
    assert all(set(case) == _PAYLOAD_CASE_KEYS for case in payload_cases)
    assert all(set(case) == _CREATION_CASE_KEYS for case in creation_cases)
    assert all(set(case) == _TRANSITION_CASE_KEYS for case in transition_cases)
    assert all(
        set(case["expected"]) == _CREATION_EXPECTED_KEYS
        for case in creation_cases
    )
    assert all(
        set(case["expected"]) == _TRANSITION_EXPECTED_KEYS
        for case in transition_cases
    )
    assert all(
        mutation["op"] in {"set", "delete", "replace_root"}
        and set(mutation) == _MUTATION_KEYS
        for case in payload_cases
        for mutation in case["mutations"]
    )


def test_canonical_proposal_payloads_are_minimal_and_privacy_safe():
    canonical_payloads = _load_fixture()["canonical_payloads"]

    for payload in canonical_payloads.values():
        assert _reference_payload_errors(payload) == []
        assert _nested_keys(payload).isdisjoint(_FORBIDDEN_PAYLOAD_KEYS)
        assert _FINGERPRINT_RE.fullmatch(
            payload["target"]["base_plan_fingerprint"]
        )
        assert payload["before"] != payload["after"]
        assert payload["changes"]
        assert payload["evidence"]
        assert all(
            _FINGERPRINT_RE.fullmatch(item["result_fingerprint"])
            for item in payload["evidence"]
        )


def test_payload_cases_define_strict_schema_and_safety_failures():
    fixture = _load_fixture()
    canonical_payloads = fixture["canonical_payloads"]

    for case in fixture["payload_cases"]:
        payload = _apply_mutations(
            canonical_payloads[case["base_payload"]],
            case["mutations"],
        )
        assert _reference_payload_errors(payload) == case[
            "expected_error_codes"
        ]

    assert {
        code
        for case in fixture["payload_cases"]
        for code in case["expected_error_codes"]
    } == _PAYLOAD_ERROR_CODES


def test_payload_schema_matches_every_fixed_contract_case():
    fixture = _load_fixture()
    canonical_payloads = fixture["canonical_payloads"]

    for case in fixture["payload_cases"]:
        payload = _apply_mutations(
            canonical_payloads[case["base_payload"]],
            case["mutations"],
        )
        assert list(plan_adjustment_proposal_payload_error_codes(payload)) == (
            case["expected_error_codes"]
        )


def test_canonical_payload_schemas_are_strict_immutable_and_json_safe():
    for payload in _load_fixture()["canonical_payloads"].values():
        validated = PlanAdjustmentProposalPayload.model_validate(payload)

        assert validated.model_dump(mode="json", exclude_unset=True) == payload
        assert validated.target.resource_type == "workout_plan"
        assert validated.proposal_type == "plan_adjustment_v1"
        assert validated.before != validated.after
        with pytest.raises(ValidationError, match="Instance is frozen"):
            validated.proposal_type = "plan_adjustment_v1"


def test_creation_gate_cases_define_every_rejection_reason_and_ttl_boundary():
    cases = _load_fixture()["creation_gate_cases"]

    for case in cases:
        assert _reference_creation_gate(case) == case["expected"]

    assert {
        case["expected"]["reason_code"]
        for case in cases
        if case["expected"]["reason_code"] is not None
    } == _CREATION_GATE_REASON_CODES
    assert {
        case["expected"]["ttl_hours"]
        for case in cases
        if case["expected"]["eligible"]
    } == {24, 72}
    assert all(
        case["expected"]["initial_status"] == "pending_confirmation"
        for case in cases
        if case["expected"]["eligible"]
    )


def test_creation_gate_matches_every_fixed_contract_case():
    for case in _load_fixture()["creation_gate_cases"]:
        actual = evaluate_plan_adjustment_proposal_creation(**{
            key: value
            for key, value in case.items()
            if key not in {"case_id", "expected"}
        })

        assert actual.model_dump(mode="json") == case["expected"]


def test_transition_cases_define_atomic_idempotent_state_machine():
    cases = _load_fixture()["transition_cases"]

    for case in cases:
        assert case["current_status"] in _PROPOSAL_STATUSES
        assert case["action"] in {"confirm", "reject"}
        assert _reference_transition(case) == case["expected"]

    assert {
        case["expected"]["error_code"]
        for case in cases
        if case["expected"]["error_code"] is not None
    } == _TRANSITION_ERROR_CODES
    assert {
        case["expected"]["next_status"]
        for case in cases
        if case["expected"]["next_status"] in _TERMINAL_STATUSES
    } == _TERMINAL_STATUSES


def test_only_one_transition_case_writes_and_all_replays_are_write_free():
    cases = _load_fixture()["transition_cases"]
    writing_cases = [
        case
        for case in cases
        if case["expected"]["business_write_count"] > 0
    ]

    assert [case["case_id"] for case in writing_cases] == [
        "confirm.valid_applies_once"
    ]
    assert writing_cases[0]["expected"]["business_write_count"] == 1
    assert all(
        case["expected"]["business_write_count"] == 0
        for case in cases
        if case["idempotency_replay"]
        or case["expected"]["result"] != "applied"
    )


def test_contract_cases_contain_no_real_user_or_runtime_payloads():
    fixture = _load_fixture()
    serialized = json.dumps(fixture, ensure_ascii=False).lower()

    assert "@" not in serialized
    assert "bearer " not in serialized
    assert "authorization" not in serialized
    assert "access_token" not in serialized
    assert "refresh_token" not in serialized
    assert "raw_prompt" not in _nested_keys(fixture["canonical_payloads"])
    assert "raw_result" not in _nested_keys(fixture["canonical_payloads"])
