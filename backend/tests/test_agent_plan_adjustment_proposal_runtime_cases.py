from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.agent_plan_adjustment_proposals import (
    evaluate_plan_adjustment_proposal_creation,
)


_CASES_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "agent_plan_adjustment_proposal_runtime_cases.json"
)
_GATE_KEYS = {
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
}
_CASE_KEYS = {
    "case_id",
    "overrides",
    "persistence_outcome",
    "expected",
}
_EXPECTED_KEYS = {
    "creation",
    "persistence_called",
    "transaction_outcome",
    "durable",
    "response",
}
_CREATION_KEYS = {"eligible", "reason_code"}
_DURABLE_KEYS = {
    "run_status",
    "run_error_code",
    "assistant_message_count",
    "proposal_count",
}
_RESPONSE_KEYS = {
    "http_status",
    "top_level_shape",
    "proposal_reference",
    "legacy_reply_cards_trace_unchanged",
}
_PERSISTENCE_OUTCOMES = {
    "must_not_be_called",
    "created",
    "replayed",
    "error",
}
_FORBIDDEN_FACT_KEYS = {
    "user_id",
    "conversation_id",
    "run_id",
    "message",
    "prompt",
    "reply",
    "payload_data",
}


def _load_fixture() -> dict[str, Any]:
    payload = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    assert set(payload) == {
        "fixture_version",
        "gate_defaults",
        "response_contract",
        "cases",
    }
    assert payload["fixture_version"] == "1.0.0"
    return payload


def _merged_facts(
    defaults: dict[str, Any],
    case: dict[str, Any],
) -> dict[str, Any]:
    return {**defaults, **case["overrides"]}


def _reference_expected(
    facts: dict[str, Any],
    persistence_outcome: str,
) -> dict[str, Any]:
    decision = evaluate_plan_adjustment_proposal_creation(**facts)
    creation = {
        "eligible": decision.eligible,
        "reason_code": decision.reason_code,
    }
    if not decision.eligible:
        assert persistence_outcome == "must_not_be_called"
        return {
            "creation": creation,
            "persistence_called": False,
            "transaction_outcome": "commit_legacy_result",
            "durable": {
                "run_status": "completed",
                "run_error_code": None,
                "assistant_message_count": 1,
                "proposal_count": 0,
            },
            "response": {
                "http_status": 200,
                "top_level_shape": "legacy_exact",
                "proposal_reference": "absent",
                "legacy_reply_cards_trace_unchanged": True,
            },
        }

    assert persistence_outcome in {"created", "replayed", "error"}
    if persistence_outcome == "error":
        return {
            "creation": creation,
            "persistence_called": True,
            "transaction_outcome": "rollback_result_then_mark_failed",
            "durable": {
                "run_status": "failed",
                "run_error_code": "agent_runtime_error",
                "assistant_message_count": 0,
                "proposal_count": 0,
            },
            "response": {
                "http_status": 503,
                "top_level_shape": "error",
                "proposal_reference": "absent",
                "legacy_reply_cards_trace_unchanged": False,
            },
        }

    return {
        "creation": creation,
        "persistence_called": True,
        "transaction_outcome": "commit_result_with_proposal",
        "durable": {
            "run_status": "completed",
            "run_error_code": None,
            "assistant_message_count": 1,
            "proposal_count": 1,
        },
        "response": {
            "http_status": 200,
            "top_level_shape": "legacy_plus_optional_proposal",
            "proposal_reference": "present",
            "legacy_reply_cards_trace_unchanged": True,
        },
    }


def test_runtime_cases_are_well_formed_bounded_and_privacy_safe():
    fixture = _load_fixture()
    defaults = fixture["gate_defaults"]
    cases = fixture["cases"]

    assert set(defaults) == _GATE_KEYS
    assert len(cases) == 11
    assert len({case["case_id"] for case in cases}) == len(cases)
    assert set(defaults).isdisjoint(_FORBIDDEN_FACT_KEYS)

    for case in cases:
        assert set(case) == _CASE_KEYS
        assert set(case["overrides"]) <= _GATE_KEYS
        assert set(case["overrides"]).isdisjoint(_FORBIDDEN_FACT_KEYS)
        assert case["persistence_outcome"] in _PERSISTENCE_OUTCOMES
        assert set(case["expected"]) == _EXPECTED_KEYS
        assert set(case["expected"]["creation"]) == _CREATION_KEYS
        assert set(case["expected"]["durable"]) == _DURABLE_KEYS
        assert set(case["expected"]["response"]) == _RESPONSE_KEYS


def test_runtime_cases_cover_trigger_parity_replay_and_rollback():
    fixture = _load_fixture()
    cases = fixture["cases"]
    case_ids = {case["case_id"] for case in cases}

    assert {
        "flag_off.adjustment_text_is_exact_legacy_parity",
        "flag_on.valid_adjustment_creates_one_reference",
        "flag_on.worker_replay_returns_same_reference",
        "flag_on.informational_answer_does_not_create",
        "flag_on.no_change_needed_does_not_create",
        "flag_on.insufficient_evidence_does_not_create",
        "flag_on.health_red_flag_does_not_create",
        "flag_on.unresolved_clarification_does_not_create",
        "flag_on.missing_active_plan_does_not_create",
        "flag_on.invalid_draft_does_not_create",
        "flag_on.persistence_error_rolls_back_final_result",
    } == case_ids
    assert {
        case["persistence_outcome"] for case in cases
    } == _PERSISTENCE_OUTCOMES


def test_runtime_case_expectations_follow_creation_and_transaction_contract():
    fixture = _load_fixture()
    defaults = fixture["gate_defaults"]

    for case in fixture["cases"]:
        facts = _merged_facts(defaults, case)
        assert case["expected"] == _reference_expected(
            facts,
            case["persistence_outcome"],
        )


def test_flag_off_and_non_created_cases_preserve_exact_legacy_response():
    fixture = _load_fixture()

    for case in fixture["cases"]:
        expected = case["expected"]
        if expected["durable"]["run_status"] != "completed":
            continue
        if expected["durable"]["proposal_count"] == 0:
            assert expected["response"] == {
                "http_status": 200,
                "top_level_shape": "legacy_exact",
                "proposal_reference": "absent",
                "legacy_reply_cards_trace_unchanged": True,
            }
            assert expected["persistence_called"] is False


def test_created_and_replayed_proposals_add_only_the_optional_reference():
    fixture = _load_fixture()
    successful_proposals = [
        case
        for case in fixture["cases"]
        if case["persistence_outcome"] in {"created", "replayed"}
    ]

    assert len(successful_proposals) == 2
    for case in successful_proposals:
        expected = case["expected"]
        assert expected["durable"] == {
            "run_status": "completed",
            "run_error_code": None,
            "assistant_message_count": 1,
            "proposal_count": 1,
        }
        assert expected["response"] == {
            "http_status": 200,
            "top_level_shape": "legacy_plus_optional_proposal",
            "proposal_reference": "present",
            "legacy_reply_cards_trace_unchanged": True,
        }


def test_persistence_error_rolls_back_message_and_proposal_before_run_failure():
    fixture = _load_fixture()
    failure_cases = [
        case
        for case in fixture["cases"]
        if case["persistence_outcome"] == "error"
    ]

    assert len(failure_cases) == 1
    expected = failure_cases[0]["expected"]
    assert expected["transaction_outcome"] == (
        "rollback_result_then_mark_failed"
    )
    assert expected["durable"] == {
        "run_status": "failed",
        "run_error_code": "agent_runtime_error",
        "assistant_message_count": 0,
        "proposal_count": 0,
    }
    assert expected["response"]["http_status"] == 503
    assert expected["response"]["proposal_reference"] == "absent"


def test_optional_response_reference_is_minimal_and_omitted_when_absent():
    contract = _load_fixture()["response_contract"]

    assert contract == {
        "transport_field": "proposal",
        "success_surfaces": ["AgentChatResponse", "AgentRunResponse"],
        "reference_keys": [
            "id",
            "proposal_type",
            "status",
            "version",
            "expires_at",
            "payload_fingerprint",
        ],
        "excluded_reference_keys": [
            "payload_data",
            "before",
            "after",
            "changes",
            "evidence",
        ],
        "omission_policy": "omit_when_not_created",
    }
    assert set(contract["reference_keys"]).isdisjoint(
        contract["excluded_reference_keys"]
    )
