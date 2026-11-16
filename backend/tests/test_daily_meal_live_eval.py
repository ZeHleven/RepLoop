from argparse import Namespace
import json
from pathlib import Path
import uuid

from sqlalchemy import select

from app.models.profile import UserProfile, WeightLog
from app.models.user import User
from scripts import evaluate_daily_meal_generation_real as live_eval


def _complete_food_rows():
    return [(
        str(item["name_zh"]),
        str(item["category"]),
        float(item["calories_per_100g"]),
        float(item["protein_g"]),
        float(item["carbs_g"]),
        float(item["fat_g"]),
    ) for item in live_eval.BASELINE_FOODS]


def test_live_eval_food_catalog_prerequisite_accepts_baseline_catalog():
    diagnostics = live_eval._food_catalog_prerequisite_diagnostics(
        _complete_food_rows()
    )

    assert diagnostics["active_food_count"] == 20
    assert diagnostics["missing_seed_food_count"] == 0
    assert diagnostics["invalid_nutrition_count"] == 0
    assert all(
        count > 0
        for count in diagnostics["required_category_counts"].values()
    )


def test_live_eval_food_catalog_prerequisite_exposes_incomplete_fixture():
    diagnostics = live_eval._food_catalog_prerequisite_diagnostics([
        ("杂粮饭", "碳水", 130.0, 3.0, 28.0, 1.0),
    ])

    assert diagnostics == {
        "active_food_count": 1,
        "required_seed_food_count": 20,
        "missing_seed_food_count": 20,
        "invalid_nutrition_count": 0,
        "required_category_counts": {
            "蛋白质": 0,
            "碳水": 1,
            "蔬菜": 0,
            "油脂": 0,
        },
    }


def test_live_gate_seeds_food_catalog_before_model_evaluation():
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "daily-meal-live-eval.yml"
    ).read_text(encoding="utf-8")

    seed_index = workflow.index("python scripts/seed_foods.py")
    evaluation_index = workflow.index(
        "python backend/scripts/evaluate_daily_meal_generation_real.py"
    )
    assert seed_index < evaluation_index


def test_live_gate_checks_out_an_owner_selected_immutable_revision():
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "daily-meal-live-eval.yml"
    ).read_text(encoding="utf-8")

    assert "candidate_sha:" in workflow
    assert "if: github.actor == github.repository_owner" in workflow
    assert "^[0-9a-fA-F]{40}$" in workflow
    assert "ref: ${{ inputs.candidate_sha }}" in workflow
    assert "if: github.ref == 'refs/heads/main'" not in workflow
    assert "evaluated_sha=\"$(git rev-parse HEAD)\"" in workflow
    assert '"${evaluated_sha}" != "${REQUESTED_SHA,,}"' in workflow
    assert "daily-meal-live-eval-${{ steps.revision.outputs.sha }}" in workflow
    assert "evaluate_agent_routing_real.py" in workflow
    assert "--repeat 2" in workflow
    assert "timeout-minutes: 60" in workflow
    assert "id: route_preflight" in workflow
    assert "id: daily_meal_gate" in workflow
    assert workflow.count("continue-on-error: true") == 2
    assert "ROUTE_OUTCOME: ${{ steps.route_preflight.outcome }}" in workflow
    assert "DAILY_MEAL_OUTCOME: ${{ steps.daily_meal_gate.outcome }}" in workflow
    assert workflow.index("Upload sanitized evaluation report") < workflow.index(
        "Enforce combined live release verdict"
    )


async def test_live_eval_seed_persists_user_before_dependent_records(db_session):
    suffix = f"test-{uuid.uuid4().hex[:12]}"

    user_id = await live_eval._seed_evaluation_subject(
        db_session,
        suffix=suffix,
    )

    assert await db_session.get(User, user_id) is not None
    assert await db_session.scalar(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ) is not None
    assert await db_session.scalar(
        select(WeightLog).where(WeightLog.user_id == user_id)
    ) is not None


def test_live_eval_writes_sanitized_report_for_fatal_evaluation_error(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "live-eval.json"

    async def fail_evaluation():
        raise RuntimeError("sensitive database and user details")

    monkeypatch.setattr(live_eval.settings, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(live_eval, "evaluate", fail_evaluation)
    monkeypatch.setattr(
        live_eval,
        "parse_args",
        lambda: Namespace(
            strict=True, output=output, repeat=1, candidate_sha="a" * 40
        ),
    )

    assert live_eval.main() == 1
    report_text = output.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report["passed"] is False
    assert report["fatal_error"] == {
        "stage": "evaluation",
        "type": "RuntimeError",
    }
    assert report["intent_timeout_runs"] == 0
    assert report["rules_fallback_runs"] == 0
    assert report["semantic_misroute_runs"] == 0
    assert report["optimizer_unavailable_runs"] == 0
    assert "sensitive database and user details" not in report_text


def test_live_eval_reports_food_catalog_prerequisite_failure(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "live-eval.json"
    diagnostics = {
        "active_food_count": 1,
        "required_seed_food_count": 20,
        "missing_seed_food_count": 20,
        "invalid_nutrition_count": 0,
        "required_category_counts": {
            "蛋白质": 0,
            "碳水": 1,
            "蔬菜": 0,
            "油脂": 0,
        },
    }

    async def fail_prerequisites():
        raise live_eval.LiveEvaluationPrerequisiteError(diagnostics)

    monkeypatch.setattr(live_eval.settings, "DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(live_eval, "evaluate", fail_prerequisites)
    monkeypatch.setattr(
        live_eval,
        "parse_args",
        lambda: Namespace(
            strict=True, output=output, repeat=1, candidate_sha="a" * 40
        ),
    )

    assert live_eval.main() == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["fatal_error"] == {
        "stage": "prerequisites",
        "type": "food_catalog_incomplete",
    }
    assert report["prerequisites"] == diagnostics


def test_live_eval_missing_key_still_writes_diagnostic_artifact(
    monkeypatch,
    tmp_path,
):
    output = tmp_path / "live-eval.json"
    monkeypatch.setattr(live_eval.settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(
        live_eval,
        "parse_args",
        lambda: Namespace(
            strict=True, output=output, repeat=1, candidate_sha="a" * 40
        ),
    )

    assert live_eval.main() == 2
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["fatal_error"] == {
        "stage": "configuration",
        "type": "missing_deepseek_api_key",
    }
    assert report["candidate_sha"] == "a" * 40


async def test_repeated_gate_requires_every_pass_to_succeed(monkeypatch):
    reports = [
        {"passed": True, "original_successes": 10, "original_runs": 10,
         "synonym_successes": 20, "synonym_runs": 20,
         "generation_as_mutation_count": 0, "unconfirmed_meal_writes": 0,
         "proposal_count": 0, "rules_fallback_runs": 0,
         "optimizer_unavailable_runs": 0},
        {"passed": False, "original_successes": 10, "original_runs": 10,
         "synonym_successes": 18, "synonym_runs": 20,
         "generation_as_mutation_count": 0, "unconfirmed_meal_writes": 0,
         "proposal_count": 0, "rules_fallback_runs": 0,
         "optimizer_unavailable_runs": 0},
    ]

    async def fake_evaluate(*, pass_index):
        return reports[pass_index - 1]

    monkeypatch.setattr(live_eval, "evaluate", fake_evaluate)
    report = await live_eval.evaluate_repeated(2)

    assert report["passed"] is False
    assert len(report["passes"]) == 2


def test_report_decorator_records_candidate_and_router_for_each_pass():
    report = {"passed": True, "passes": [{"pass_index": 1}, {"pass_index": 2}]}

    decorated = live_eval._decorate_report(
        report,
        candidate_sha="a" * 40,
    )

    assert decorated["candidate_sha"] == "a" * 40
    assert all(
        item["candidate_sha"] == "a" * 40
        and item["router_version"] == "semantic_route_v2"
        for item in decorated["passes"]
    )
