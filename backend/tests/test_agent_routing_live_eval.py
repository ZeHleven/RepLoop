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


# R157: exercise the evaluation entry point with a real isolated SQL account.
import pytest
from sqlalchemy import select
from app.config import settings
from app.models.user import User
from app.models.model_budget import ModelBudget, ModelCharge
from app.services import model_budget as budget


async def test_route_eval_binds_one_persisted_account_for_all_cases(
    monkeypatch, session_factory,
):
    monkeypatch.setattr(route_eval, "AsyncSessionLocal", session_factory, raising=False)
    monkeypatch.setattr(budget, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(route_eval, "CASES", (
        route_eval.CASES[0],
        route_eval.RouteCase("profile.read.again", "synthetic repeat", "profile", "query", "read"),
    ))
    seen = []

    async def resolve(message, *, use_model):
        scope = budget._scope.get()
        assert scope is not None, "live route evaluation omitted billing identity"
        async with session_factory() as db:
            assert await db.get(User, scope.user_id) is not None
        seen.append(scope.user_id)
        await budget.reserve(scope.user_id, "deepseek-v4-flash", 100_000)
        return IntentResolverOutcome(
            resolution=IntentResolution(
                primary_intent="profile_query", intent_domain="profile",
                request_kind="query", requested_effect="read", requested_output="answer",
                risk_level="low", confidence=1,
            ), source="model", attempt_count=1,
        )

    monkeypatch.setattr(route_eval, "resolve_intent_with_fallback", resolve)
    report = await route_eval.evaluate()
    assert report["passed"] and report["total"] == 2
    assert len(set(seen)) == 1 and budget._scope.get() is None
    async with session_factory() as db:
        row = await db.get(ModelBudget, seen[0])
        charges = list((await db.scalars(select(ModelCharge).where(
            ModelCharge.user_id == seen[0],
        ))).all())
    assert row.reserved_nano == 200_000 and len(charges) == 2
    print("R157_ROUTE_ACCOUNT=" + json.dumps({
        "case_count": 2, "unique_accounts": 1,
        "reserved_nano": row.reserved_nano, "charges": len(charges),
    }))


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://synthetic@db.example.invalid/fitness_live_eval",
    "postgresql+asyncpg://synthetic@127.0.0.1/fitness",
    "postgresql+asyncpg://synthetic@127.0.0.1/fitness_test?host=remote",
    "sqlite+aiosqlite:///fitness_test",
])
async def test_route_eval_rejects_nonisolated_database_before_work(monkeypatch, url):
    monkeypatch.setattr(settings, "DATABASE_URL", url)

    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe evaluation reached database or model")

    monkeypatch.setattr(route_eval, "AsyncSessionLocal", forbidden, raising=False)
    monkeypatch.setattr(route_eval, "resolve_intent_with_fallback", forbidden)
    with pytest.raises(ValueError, match="isolated"):
        await route_eval.evaluate()


def test_route_eval_missing_key_preserves_candidate_and_never_seeds(
    monkeypatch, tmp_path,
):
    import sys
    candidate = "51feceb570b8d0d374948c5bf0558201958d1d4f"
    output = tmp_path / "report.json"
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(sys, "argv", [
        "route-eval", "--strict", "--candidate-sha", candidate, "--output", str(output),
    ])

    def forbidden(*args, **kwargs):
        raise AssertionError("missing-key preflight created an account")

    monkeypatch.setattr(route_eval, "AsyncSessionLocal", forbidden, raising=False)
    assert route_eval.main() == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["candidate_sha"] == candidate and report["passed"] is False
    assert report["fatal_error"]["type"] == "missing_deepseek_api_key"
