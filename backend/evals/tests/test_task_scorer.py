from copy import deepcopy

import pytest

from evals.task_contracts import Check, TaskCase, load_tasks, score_task


def _case():
    return TaskCase(id="scorer_probe", split="development", scenario="probe", message="保存",
                    checks=[Check(dimension="evidence", path="evidence", expected="complete"),
                            Check(dimension="persistence", path="meal_count", expected=3),
                            Check(dimension="task_state", path="status", expected="applied"),
                            Check(dimension="facts", path="date", expected="2026-09-29"),
                            Check(dimension="presentation", path="card_count", expected=3),
                            Check(dimension="semantic", path="kind", expected="generation")])


def _record():
    return {"model_mode": "scripted", "observed": {
        "evidence": "complete", "meal_count": 3, "status": "applied",
        "date": "2026-09-29", "card_count": 3, "kind": "generation"}}


@pytest.mark.parametrize(("field", "value"), [
    ("evidence", "partial"), ("meal_count", 6), ("status", "completed"),
    ("date", "2026-09-28"), ("card_count", 0), ("meal_count", True),
])
def test_completed_process_cannot_hide_task_failure(field, value):
    record = _record()
    record["observed"][field] = value
    record["status"] = "completed"
    assert not score_task(_case(), record)["passed"]


def test_missing_or_incomplete_records_are_never_success():
    assert not score_task(_case(), None)["passed"]
    record = _record()
    del record["observed"]["evidence"]
    assert not score_task(_case(), record)["passed"]


def test_replay_cannot_relabel_scripted_results_as_live(tmp_path):
    import json
    from evals.task_contracts import build_report
    case = _case()
    (tmp_path / (case.id + ".json")).write_text(json.dumps(_record()), encoding="utf-8")
    report = build_report([case], tmp_path, expected_model_mode="live")
    assert report["failed"] == 1
    assert report["cases"][0]["error"] == "model_mode_mismatch"
    record = _record()
    record["model_mode"] = "unknown"
    assert not score_task(case, record)["passed"]
    record = _record()
    record["error"] = {"type": "RunnerError"}
    assert not score_task(_case(), record)["passed"]


def test_scripted_semantics_are_unmeasured_and_live_semantics_are_required():
    record = _record()
    record["observed"]["kind"] = "wrong"
    score = score_task(_case(), record)
    assert score["passed"]
    assert score["dimensions"]["semantic"] == "not_run"
    record["model_mode"] = "live"
    assert not score_task(_case(), record)["passed"]


def test_frozen_matrix_contains_both_splits_and_complete_write_lifecycles():
    dataset = load_tasks()
    assert {case.split for case in dataset.cases} == {"development", "holdout"}
    assert any(check.path == "writes.after_replay" for case in dataset.cases for check in case.checks)


def test_original_live_scope_hallucination_is_rejected():
    from evals.nutrition_answer_checks import history_scope_claim_is_consistent
    data = {"days": [{"date": "2026-09-29"}, {"date": "2026-08-01"}], "scope": {"as_of": "2026-09-29"}}
    original = "近 30 天内有记录的饮食日期只有 2 天：2026-09-29、2026-08-01"
    assert not history_scope_claim_is_consistent(original, data)
    assert history_scope_claim_is_consistent("最近有记录的2个日期：2026-09-29、2026-08-01", data)
