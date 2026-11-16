from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_api_cases.json"
)
_TOP_LEVEL_KEYS = {
    "fixture_version",
    "endpoint_contract",
    "read_cases",
    "request_validation_cases",
    "decision_cases",
    "concurrency_cases",
}
_READ_CASE_KEYS = {
    "case_id",
    "owner_state",
    "stored_status",
    "time_state",
    "expected",
}
_READ_EXPECTED_KEYS = {
    "http_status",
    "error_code",
    "effective_status",
    "allowed_actions",
    "result_present",
    "durable_write_count",
}
_VALIDATION_CASE_KEYS = {
    "case_id",
    "body",
    "expected_http_status",
}
_DECISION_CASE_KEYS = {
    "case_id",
    "action",
    "owner_state",
    "feature_enabled",
    "current_status",
    "expected_version",
    "actual_version",
    "client_request_state",
    "time_state",
    "expected",
}
_DECISION_EXPECTED_KEYS = {
    "http_status",
    "result",
    "error_code",
    "next_status",
    "next_version",
    "applied",
    "business_write_count",
    "state_write_count",
    "response_replay",
}
_CONCURRENCY_CASE_KEYS = {
    "case_id",
    "requests",
    "expected_invariants",
}
_CONCURRENCY_INVARIANT_KEYS = {
    "allowed_terminal_statuses",
    "success_response_count",
    "conflict_response_count",
    "business_write_count_max",
    "new_plan_count_max",
    "active_plan_count",
    "distinct_result_plan_ids_max",
}
_STATUSES = {
    "pending_confirmation",
    "applied",
    "rejected",
    "expired",
    "stale",
    "failed",
}


def _load_fixture() -> dict[str, Any]:
    fixture = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    assert set(fixture) == _TOP_LEVEL_KEYS
    assert fixture["fixture_version"] == "1.0.0"
    return fixture


def _read_result(
    *,
    http_status: int,
    error_code: str | None,
    effective_status: str | None,
    allowed_actions: list[str] | None = None,
    result_present: bool = False,
) -> dict[str, Any]:
    return {
        "http_status": http_status,
        "error_code": error_code,
        "effective_status": effective_status,
        "allowed_actions": allowed_actions or [],
        "result_present": result_present,
        "durable_write_count": 0,
    }


def _reference_read(case: dict[str, Any]) -> dict[str, Any]:
    if case["owner_state"] != "owned":
        return _read_result(
            http_status=404,
            error_code="proposal_not_found",
            effective_status=None,
        )

    status = case["stored_status"]
    if (
        status == "pending_confirmation"
        and case["time_state"] == "at_or_after_expiry"
    ):
        return _read_result(
            http_status=200,
            error_code=None,
            effective_status="expired",
        )
    return _read_result(
        http_status=200,
        error_code=None,
        effective_status=status,
        allowed_actions=(
            ["confirm", "reject"]
            if status == "pending_confirmation"
            else []
        ),
        result_present=status == "applied",
    )


def _decision_result(
    *,
    http_status: int,
    result: str,
    error_code: str | None,
    next_status: str | None,
    next_version: int | None,
    applied: bool = False,
    business_write_count: int = 0,
    state_write_count: int = 0,
    response_replay: bool = False,
) -> dict[str, Any]:
    return {
        "http_status": http_status,
        "result": result,
        "error_code": error_code,
        "next_status": next_status,
        "next_version": next_version,
        "applied": applied,
        "business_write_count": business_write_count,
        "state_write_count": state_write_count,
        "response_replay": response_replay,
    }


def _reference_decision(case: dict[str, Any]) -> dict[str, Any]:
    status = case["current_status"]
    version = case["actual_version"]
    action = case["action"]
    if case["owner_state"] != "owned":
        return _decision_result(
            http_status=404,
            result="not_found",
            error_code="proposal_not_found",
            next_status=None,
            next_version=None,
        )
    if not case["feature_enabled"]:
        return _decision_result(
            http_status=503,
            result="disabled",
            error_code="proposal_feature_disabled",
            next_status=status,
            next_version=version,
        )
    if case["client_request_state"] == "same_replay" and (
        (status == "applied" and action == "confirm")
        or (status == "rejected" and action == "reject")
    ):
        return _decision_result(
            http_status=200,
            result="idempotent",
            error_code=None,
            next_status=status,
            next_version=version,
            applied=status == "applied",
            response_replay=True,
        )
    if status == "applied" and action == "confirm":
        return _decision_result(
            http_status=200,
            result="idempotent",
            error_code=None,
            next_status=status,
            next_version=version,
            applied=True,
            response_replay=True,
        )
    if status != "pending_confirmation":
        return _decision_result(
            http_status=409,
            result="conflict",
            error_code="proposal_not_pending",
            next_status=status,
            next_version=version,
            applied=status == "applied",
        )
    if case["expected_version"] != version:
        return _decision_result(
            http_status=409,
            result="conflict",
            error_code="proposal_version_conflict",
            next_status=status,
            next_version=version,
        )
    if case["time_state"] == "at_or_after_expiry":
        return _decision_result(
            http_status=409,
            result="expired",
            error_code="proposal_expired",
            next_status="expired",
            next_version=version + 1,
            state_write_count=1,
        )
    if action == "reject":
        return _decision_result(
            http_status=200,
            result="rejected",
            error_code=None,
            next_status="rejected",
            next_version=version + 1,
            state_write_count=1,
        )
    return _decision_result(
        http_status=200,
        result="applied",
        error_code=None,
        next_status="applied",
        next_version=version + 1,
        applied=True,
        business_write_count=1,
        state_write_count=1,
    )


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


def test_api_fixture_is_well_formed_bounded_and_unique():
    fixture = _load_fixture()
    read_cases = fixture["read_cases"]
    validation_cases = fixture["request_validation_cases"]
    decision_cases = fixture["decision_cases"]
    concurrency_cases = fixture["concurrency_cases"]

    assert len(read_cases) == 6
    assert len(validation_cases) == 5
    assert len(decision_cases) == 15
    assert len(concurrency_cases) == 4
    all_cases = [
        *read_cases,
        *validation_cases,
        *decision_cases,
        *concurrency_cases,
    ]
    assert len({case["case_id"] for case in all_cases}) == len(all_cases)
    assert all(set(case) == _READ_CASE_KEYS for case in read_cases)
    assert all(
        set(case["expected"]) == _READ_EXPECTED_KEYS
        for case in read_cases
    )
    assert all(
        set(case) == _VALIDATION_CASE_KEYS for case in validation_cases
    )
    assert all(set(case) == _DECISION_CASE_KEYS for case in decision_cases)
    assert all(
        set(case["expected"]) == _DECISION_EXPECTED_KEYS
        for case in decision_cases
    )
    assert all(
        set(case) == _CONCURRENCY_CASE_KEYS for case in concurrency_cases
    )
    assert all(
        set(case["expected_invariants"])
        == _CONCURRENCY_INVARIANT_KEYS
        for case in concurrency_cases
    )


def test_endpoint_contract_uses_authenticated_dedicated_routes():
    contract = _load_fixture()["endpoint_contract"]

    assert contract["read"] == {
        "method": "GET",
        "path": "/api/v1/agent/proposals/{proposal_id}",
        "auth_source": "jwt_current_user",
        "request_body_keys": [],
        "success_status": 200,
        "response_keys": [
            "id",
            "proposal_type",
            "status",
            "version",
            "payload_fingerprint",
            "payload",
            "expires_at",
            "created_at",
            "updated_at",
            "allowed_actions",
            "result",
        ],
        "payload_keys": [
            "schema_version",
            "proposal_type",
            "target",
            "before",
            "after",
            "changes",
            "evidence",
            "rationale",
            "safety_notes",
        ],
    }
    for action in ("confirm", "reject"):
        assert contract[action] == {
            "method": "POST",
            "path": f"/api/v1/agent/proposals/{{proposal_id}}/{action}",
            "auth_source": "jwt_current_user",
            "success_status": 200,
        }


def test_decision_request_is_strict_cas_and_cannot_inject_identity():
    contract = _load_fixture()["endpoint_contract"]["decision_request"]

    assert contract == {
        "keys": ["expected_version", "client_request_id"],
        "expected_version": {"type": "strict_integer", "minimum": 1},
        "client_request_id": {
            "type": "strict_string",
            "minimum_length": 8,
            "maximum_length": 120,
        },
        "unknown_fields": "forbidden",
        "user_id_source": "jwt_only",
    }
    cases = _load_fixture()["request_validation_cases"]
    assert all(case["expected_http_status"] == 422 for case in cases)
    assert any(isinstance(case["body"]["expected_version"], bool) for case in cases)
    assert any("user_id" in case["body"] for case in cases)
    assert any(
        set(case["body"]) - {"expected_version", "client_request_id", "user_id"}
        for case in cases
    )


def test_read_cases_define_owner_isolation_and_read_only_expiry_projection():
    cases = _load_fixture()["read_cases"]

    for case in cases:
        assert case["owner_state"] in {"owned", "foreign", "missing"}
        assert (
            case["stored_status"] in _STATUSES
            or case["stored_status"] is None
        )
        assert _reference_read(case) == case["expected"]

    hidden = [case for case in cases if case["owner_state"] != "owned"]
    assert len(hidden) == 2
    assert {case["expected"]["http_status"] for case in hidden} == {404}
    assert {case["expected"]["error_code"] for case in hidden} == {
        "proposal_not_found"
    }
    assert all(
        case["expected"]["durable_write_count"] == 0 for case in cases
    )


def test_decision_cases_project_cas_idempotency_expiry_and_rollback_contract():
    cases = _load_fixture()["decision_cases"]

    for case in cases:
        assert case["action"] in {"confirm", "reject"}
        assert case["current_status"] in _STATUSES
        assert case["client_request_state"] in {
            "new",
            "same_replay",
            "different_after_applied",
        }
        assert _reference_decision(case) == case["expected"]

    assert {
        case["expected"]["error_code"]
        for case in cases
        if case["expected"]["error_code"] is not None
    } == {
        "proposal_not_found",
        "proposal_not_pending",
        "proposal_version_conflict",
        "proposal_expired",
        "proposal_feature_disabled",
    }
    assert all(
        case["expected"]["business_write_count"] == 0
        for case in cases
        if case["action"] == "reject"
    )
    assert all(
        case["expected"]["business_write_count"] == 0
        for case in cases
        if case["expected"]["response_replay"]
    )


def test_expiry_uses_server_boundary_and_never_applies_or_rejects():
    fixture = _load_fixture()
    expired_read = next(
        case
        for case in fixture["read_cases"]
        if case["time_state"] == "at_or_after_expiry"
    )
    expired_decisions = [
        case
        for case in fixture["decision_cases"]
        if case["time_state"] == "at_or_after_expiry"
    ]

    assert expired_read["expected"] == _read_result(
        http_status=200,
        error_code=None,
        effective_status="expired",
    )
    assert {case["action"] for case in expired_decisions} == {
        "confirm",
        "reject",
    }
    assert all(
        case["expected"]["error_code"] == "proposal_expired"
        and case["expected"]["next_status"] == "expired"
        and case["expected"]["business_write_count"] == 0
        for case in expired_decisions
    )


def test_concurrency_cases_allow_one_terminal_transition_and_one_plan_write():
    cases = _load_fixture()["concurrency_cases"]

    for case in cases:
        requests = case["requests"]
        expected = case["expected_invariants"]
        assert len(requests) == 2
        assert all(
            set(request) == {
                "action",
                "client_request_id",
                "expected_version",
            }
            for request in requests
        )
        assert all(request["expected_version"] == 1 for request in requests)
        assert set(expected["allowed_terminal_statuses"]) <= {
            "applied",
            "rejected",
        }
        assert expected["success_response_count"] + expected[
            "conflict_response_count"
        ] == 2
        assert expected["business_write_count_max"] <= 1
        assert expected["new_plan_count_max"] <= 1
        assert expected["active_plan_count"] == 1
        assert expected["distinct_result_plan_ids_max"] <= 1

    mixed = next(
        case for case in cases if {item["action"] for item in case["requests"]}
        == {"confirm", "reject"}
    )
    assert mixed["expected_invariants"]["allowed_terminal_statuses"] == [
        "applied",
        "rejected",
    ]
    assert mixed["expected_invariants"]["success_response_count"] == 1
    assert mixed["expected_invariants"]["conflict_response_count"] == 1


def test_response_contract_is_minimal_stable_and_privacy_safe():
    contract = _load_fixture()["endpoint_contract"]
    decision_response = contract["decision_response"]
    forbidden = set(contract["privacy"]["forbidden_response_keys"])

    assert decision_response == {
        "keys": [
            "id",
            "proposal_type",
            "status",
            "version",
            "applied",
            "payload_fingerprint",
            "result_plan_id",
            "result_plan_fingerprint",
            "decided_at",
        ],
        "same_request_replay": "exact_same_body",
        "different_confirm_after_applied": "same_applied_result",
    }
    assert forbidden.isdisjoint(contract["read"]["response_keys"])
    assert forbidden.isdisjoint(decision_response["keys"])
    assert contract["business_error"]["body_keys"] == ["code", "message"]
    assert set(contract["business_error"]["status_by_code"].values()) == {
        404,
        409,
        503,
    }
    assert "user_id" not in contract["decision_request"]["keys"]
    serialized = json.dumps(_load_fixture(), ensure_ascii=False).lower()
    assert "authorization" not in serialized
    assert "bearer " not in serialized
    assert "access_token" not in serialized
    assert not (
        _nested_keys({"response": contract["read"]["response_keys"]})
        & forbidden
    )
