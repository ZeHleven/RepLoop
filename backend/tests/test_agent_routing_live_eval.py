import json

from app.services.agent_intent import IntentResolution, IntentResolverOutcome
from app.services.agent_intent_model import INTENT_ROUTE_SYSTEM_PROMPT
from scripts import evaluate_agent_routing_real as route_eval


def test_live_gate_prompts_are_not_copied_verbatim_into_production_prompt():
    leaked_case_ids = [
        case.case_id
        for case in route_eval.CASES
        if case.prompt in INTENT_ROUTE_SYSTEM_PROMPT
    ]

    assert leaked_case_ids == []


def test_health_risk_generalization_cases_cover_unseen_boundaries():
    expected = {
        "health.paraphrase.shoulder": (
            "health", "query", "read", "medium"
        ),
        "health.paraphrase.lumbar": (
            "health", "query", "read", "medium"
        ),
        "health.paraphrase.surgery": (
            "health", "mutation", "update", "medium"
        ),
        "health.paraphrase.screening": (
            "health", "query", "read", "low"
        ),
        "general.paraphrase.physiology": (
            "general", "query", "read", "low"
        ),
    }
    observed = {
        case.case_id: (
            case.domain,
            case.kind,
            case.effect,
            case.risk,
        )
        for case in route_eval.CASES
        if case.case_id in expected
    }

    assert observed == expected


def test_route_report_exposes_enum_only_expected_actual_mismatches():
    case = route_eval.RouteCase(
        "weight.read",
        "看看我最近的体重趋势",
        "profile",
        "query",
        "read",
    )
    outcome = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="health_query",
            intent_domain="health",
            request_kind="query",
            requested_effect="read",
            evidence_requirements=["weight_history"],
            requested_output="answer",
            resolved_query="sensitive normalized request",
            risk_level="medium",
            confidence=0.91,
        ),
        source="model",
        attempt_count=1,
    )

    result = route_eval._case_result(case, outcome)

    assert result["expected"] == {
        "intent_domain": "profile",
        "request_kind": "query",
        "requested_effect": "read",
        "requested_output": "answer",
        "risk_level": "low",
        "decision_action": None,
    }
    assert result["actual"] == {
        "intent_domain": "health",
        "request_kind": "query",
        "requested_effect": "read",
        "requested_output": "answer",
        "risk_level": "medium",
        "decision_action": None,
    }
    assert result["mismatch_fields"] == ["intent_domain", "risk_level"]
    assert result["read_targets"] == ["weight_history"]
    assert result["understanding_failed"] is False
    assert result["attempt_count"] == 1
    encoded = json.dumps(result, ensure_ascii=False)
    assert case.prompt not in encoded
    assert "sensitive normalized request" not in encoded


def test_route_report_marks_understanding_failure_even_when_enums_match():
    case = route_eval.RouteCase(
        "profile.read",
        "我的个人训练资料是什么",
        "profile",
        "query",
        "read",
    )
    outcome = IntentResolverOutcome(
        resolution=IntentResolution(
            primary_intent="profile_query",
            intent_domain="profile",
            request_kind="query",
            requested_effect="read",
            requested_output="answer",
            risk_level="low",
            confidence=0.65,
        ),
        source="rules",
        fallback_reason="model_unavailable",
        understanding_failed=True,
    )

    result = route_eval._case_result(case, outcome)

    assert result["mismatch_fields"] == []
    assert result["passed"] is False
    assert result["ordinary_rules_fallback"] is True
