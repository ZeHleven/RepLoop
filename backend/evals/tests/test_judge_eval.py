import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

# This test directory intentionally does not inherit backend/tests/conftest.py,
# whose autouse fixture connects to and truncates a PostgreSQL test database.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://eval:eval@localhost/unused")
os.environ.setdefault("SECRET_KEY", "eval-only-unused-secret")

from evals.judge_eval import (
    EvalCase, Fixture, Judgment, call_judge, check_judge_controls, fixture_tools, load_cases,
    run_candidate, score_judgment, summarize, trace_checks, validate_judgment, write_reports,
)


def valid_judgment():
    return {
        "dimensions": {key: {"score": 2, "reason": "Supported", "evidence_refs": ["answer"]}
                       for key in ("grounding", "completion", "context", "clarity")},
        "hard_failures": [], "summary": "Supported answer", "needs_human_review": False,
    }


def test_hard_failure_overrides_high_score():
    data = valid_judgment()
    data["hard_failures"] = [{"kind": "unauthorized_write_claim", "reason": "Claimed a write",
                              "evidence_refs": ["answer"]}]
    result = score_judgment(Judgment.model_validate(data), [], 80)
    assert result["score"] == 100 and result["passed"] is False


def test_trace_failure_and_human_review_cannot_pass():
    judgment = Judgment.model_validate(valid_judgment())
    assert not score_judgment(judgment, ["required_tool_not_successful"], 80)["passed"]
    judgment.needs_human_review = True
    assert not score_judgment(judgment, [], 80)["passed"]


@pytest.mark.parametrize("score", [True, "2", 1.5, 3, -1])
def test_judge_rejects_invalid_scores(score):
    data = valid_judgment()
    data["dimensions"]["grounding"]["score"] = score
    with pytest.raises(ValidationError):
        Judgment.model_validate(data)


def test_judge_must_cite_existing_evidence():
    data = valid_judgment()
    data["dimensions"]["grounding"]["evidence_refs"] = ["invented-tool-99"]
    with pytest.raises(ValueError):
        validate_judgment(data, ["answer"])


def test_fixture_uses_production_parameter_contract_and_exact_range():
    case = EvalCase(id="fixture", category="unit", message="query", expectations=["check"],
                    fixtures={"workout.get_progress": [Fixture(arguments={"weeks": 4}, result={"weeks": 4})]})
    trace = []
    tool = fixture_tools(case, ["workout.get_progress"], trace)[0]
    assert asyncio.run(tool.ainvoke({"weeks": 4})) == {"weeks": 4}
    assert asyncio.run(tool.ainvoke({"weeks": 8}))["error"]["code"] == "EVAL_FIXTURE_ARGUMENT_MISMATCH"
    with pytest.raises(ValidationError):
        asyncio.run(tool.ainvoke({"weeks": 53}))
    with pytest.raises(ValidationError):
        asyncio.run(tool.ainvoke({"weeks": 4, "user_id": "another-user"}))
    assert [entry["status"] for entry in trace] == ["ok", "fixture_error"]


def test_guard_branch_uses_production_reply_without_main_model():
    case = EvalCase(id="guard", category="unit", message="胸痛", use_intent_model=False,
                    expected_guard="high_risk", expectations=["stop"])
    with patch("evals.judge_eval.agent_runtime.invoke_langchain_agent", new_callable=AsyncMock) as invoke:
        candidate = asyncio.run(run_candidate(case))
    invoke.assert_not_awaited()
    assert candidate["guard"] == "high_risk" and candidate["tool_trace"] == []
    assert candidate["deterministic_violations"] == []


def test_missing_required_tool_is_not_excused_by_good_text():
    case = EvalCase(id="required", category="unit", message="query", expectations=["check"],
                    allowed_tools=["profile.get_summary"], required_tools=["profile.get_summary"])
    assert "required_tool_not_successful:profile.get_summary" in trace_checks(
        case, {"guard": "none", "allowlist": ["profile.get_summary"], "tool_trace": []})


def test_judge_failure_not_counted_as_model_quality_score():
    summary = summarize([{"status": "judge_error", "candidate": {}}])
    assert summary["judge_error_count"] == 1
    assert summary["mean_score_among_judged"] is None
    assert summary["pass_rate_among_judged"] is None


def test_judge_repairs_bad_reference_once():
    invalid = valid_judgment()
    invalid["dimensions"]["clarity"]["evidence_refs"] = ["unknown"]
    structured = AsyncMock()
    structured.ainvoke.side_effect = [
        {"parsed": Judgment.model_validate(invalid)},
        {"parsed": Judgment.model_validate(valid_judgment())},
    ]
    class FakeModel:
        def with_structured_output(self, *args, **kwargs):
            return structured
    result = asyncio.run(call_judge(FakeModel(), {"evidence_ids": ["answer"]}))
    assert result["status"] == "ok" and result["attempts"] == 2


def test_all_cases_have_valid_production_fixture_arguments():
    path = Path(__file__).resolve().parents[1] / "agent_judge_cases.json"
    cases = load_cases(path)
    assert len(cases) >= 10
    for case in cases:
        tools = fixture_tools(case, list(case.fixtures), [])
        for tool, fixtures in zip(tools, case.fixtures.values()):
            for fixture in fixtures:
                tool.args_schema.model_validate(fixture.arguments)


def test_report_retains_ungraded_errors(tmp_path):
    report = {"created_at": "test", "status": "running", "candidate_model": "candidate",
              "judge_model": "judge", "threshold": 80,
              "results": [{"sample_id": "one", "status": "candidate_error", "candidate": {}}]}
    write_reports(tmp_path, report)
    saved = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert saved["summary"]["candidate_error_count"] == 1
    assert "candidate_error" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_judge_controls_withhold_expected_labels_and_keep_separate_report(tmp_path):
    cases = load_cases(Path(__file__).resolve().parents[1] / "agent_judge_cases.json")
    judgments = [valid_judgment() for _ in range(3)]
    for data, kind in zip(judgments[1:], ["fabricated_personal_fact", "unauthorized_write_claim"]):
        data["hard_failures"] = [{"kind": kind, "reason": "Evidence contradicts answer",
                                   "evidence_refs": ["answer"]}]
    responses = [{"status": "ok", "judgment": data} for data in judgments]
    with patch("evals.judge_eval.call_judge", new=AsyncMock(side_effect=responses)) as judge:
        result = asyncio.run(check_judge_controls(None, cases, tmp_path, model_name="fake", timeout=1))
    assert result["matched_count"] == 3
    for call in judge.call_args_list:
        assert "expected_failure" not in call.args[1]
    assert not (tmp_path / "report.json").exists()
    assert (tmp_path / "judge-controls.json").exists()
