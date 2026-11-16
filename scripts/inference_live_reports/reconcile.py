"""Reconcile the preserved eight-scenario run without making model or DB calls."""
from collections import Counter
from datetime import datetime, timedelta, timezone, date
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/agent-inference-live-20261115"
SOURCE = ROOT / ".tmp/agent-inference-boundaries-20261115"
sys.path.insert(0, str(ROOT / "scripts"))
from agent_eval import source_manifest as shared_observer_manifest
from agent_eval.metrics import summarize


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest(folder):
    return {p.relative_to(folder).as_posix(): digest(p) for p in sorted(folder.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def main():
    raw = read(OUT / "live-01/result.json")
    freeze = read(OUT / "preflight-freeze.json")
    wire_freeze = read(OUT / "live-01/freeze.json")
    auth = read(OUT / "authorization.json")
    spec = read(ROOT / "scripts/inference_live/tasks.json")
    planned = read(OUT / "planned-cases-before.json")
    reviews = read(OUT / "reviews.json")
    sql = read(OUT / "postcheck/sql-postcheck.json")
    ledger = read(OUT / "live-01/ledger.json")
    fixture = read(OUT / "fixture-preflight/fixture-check.json")
    candidate = auth["candidate"]
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SOURCE, text=True).strip() == candidate
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=SOURCE).strip()
    assert candidate == raw["source_commit"] == freeze["candidate"] == wire_freeze["source_commit"]
    assert freeze["source_files"] == manifest(SOURCE / "backend")
    assert freeze["harness_files"] == manifest(ROOT / "scripts/inference_live")
    assert freeze["shared_observer_files"] == shared_observer_manifest()
    assert wire_freeze["observer_files"] == freeze["harness_files"]
    assert wire_freeze["shared_observer_files"] == freeze["shared_observer_files"]
    assert all(digest(SOURCE / path) == value for path, value in wire_freeze["source_files"].items())
    assert [c["turns"] for c in spec["cases"]] == [c["turns"] for c in planned["cases"]]
    assert spec["review_rules"] == planned["review_rules"]
    assert fixture["passed"] and fixture["http_calls"] == 0
    assert raw["business_date"] == freeze["business_date"] == fixture["business_date"]
    assert read(OUT / "live-01/source-verification.json")["before_after_equal"]
    for stage in ["offline-guard", "fixture-preflight", "live-01", "postcheck"]:
        assert read(OUT / stage / "exit.json")["exit_code"] == 0

    tasks, calls = raw["tasks"], raw["provider_calls"]
    assert len(tasks) == 8 and sum(len(t["turns"]) for t in tasks) == 10
    assert len(calls) == 42 and raw["stopped"] is None
    assert set(reviews) == {t["id"] for t in tasks}
    invalid_timings, case_results, run_ids = [], [], set()
    today = date.fromisoformat(raw["business_date"])
    week_start = today - timedelta(days=today.weekday())
    for task, case in zip(tasks, spec["cases"], strict=True):
        assert task["case_id"] == case["id"]
        assert [t["message"] for t in task["turns"]] == case["turns"]
        assert task["collection_complete"] and task["before"] == task["after"]
        assert all(t["business_after"] == task["before"] for t in task["turns"])
        assert reviews[task["id"]]["outcome"] == "completed"
        assert reviews[task["id"]]["final_reply"] == task["turns"][-1]["response"]["reply"]
        assert task["before"]["full_rows"]["user_profiles"][0]["experience_level"] is None
        expected_rest = case.get("planned_rest_seconds", {"卧推": 90, "深蹲": 90})
        assert task["before"]["plan"] == [["卧推", 3, "10", 20.0, expected_rest["卧推"]],
                                          ["深蹲", 3, "10", 30.0, expected_rest["深蹲"]]]
        assert task["before"]["plan_states"][0][2] is (not case.get("no_active_plan", False))
        sessions = [s for s in sql["session_mapping"] if s["user_id"] == task["user_id"]]
        assert len(sessions) == len(case["workouts"])
        if sessions:
            assert sorted(s["duration_min"] for s in sessions) == sorted(s["duration_min"] for s in case["workouts"])
            for session in sessions:
                assert session["status"] == "completed"
                assert session["plan_id"] == session["plan_family_id"] == task["before"]["plan_states"][0][0]
                if case["id"] == "I01":
                    assert session["week_start"] is None and session["day_of_week"] is None
                    assert session["trained_at"] == week_start.isoformat()
                else:
                    assert session["trained_at"] == session["week_start"] == (week_start - timedelta(days=7)).isoformat()
                    assert session["day_of_week"] == 1
        rest_rows = sorted((s for s in sql["planned_rest"] if s["user_id"] == task["user_id"]), key=lambda r: r["order_index"])
        assert [s["rest_seconds"] for s in rest_rows] == [expected_rest["卧推"], expected_rest["深蹲"]]
        observation = fixture["tool_observations"][case["id"]]
        if not case.get("no_active_plan"):
            assert observation["plan"]["plan"]["weekly_completed_days"] == 0
        else:
            assert observation["plan"]["found"] is False and observation["next"]["found"] is False
        for turn in task["turns"]:
            run_ids.add(turn["run_id"])
            response = turn["response"]
            assert turn["submit_status"] == 202 and response["status"] == "completed"
            assert response["error_code"] is None and response["error_message"] is None
            assert response["execution_trace"]["evidence_contract"]["status"] == "complete"
            assert not response["execution_trace"]["evidence_contract"]["missing_tools"]
            assert all(a["status"] == "completed" and a["error_code"] is None for a in turn["tool_audits"])
            if turn["visible_timing"]["ms"] is None:
                assert turn["visible_timing"]["reason"] == "clock_disagreement"
                invalid_timings.append({"case_id": case["id"], "run_id": turn["run_id"], **turn["visible_timing"]})
        task_calls = [c for c in calls if c["task_id"] == task["id"]]
        case_results.append({"case_id": case["id"], "outcome": reviews[task["id"]]["outcome"],
                             "turns": len(task["turns"]), "provider_calls": len(task_calls),
                             "cost_nano": sum(c["cost_nano"] for c in task_calls),
                             "business_unchanged": True,
                             "final_advice_visible_ms": task["turns"][-1]["visible_timing"]["ms"]})
    assert len(run_ids) == 10 and len(invalid_timings) == 3

    events = [json.loads(line) for line in (OUT / "live-01/provider-events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert Counter(e["event"] for e in events) == {"start": 42, "finish": 42}
    starts = {e["id"]: e for e in events if e["event"] == "start"}
    finishes = {e["id"]: e for e in events if e["event"] == "finish"}
    assert len(starts) == len(finishes) == len(calls)
    spent, reserved, peak_obligation = 0, 0, 0
    for event in events:
        if event["event"] == "start":
            reserved += event["reserved_nano"]
        else:
            assert event["cost_nano"] <= event["reserved_nano"]
            reserved -= event["reserved_nano"]
            spent += event["cost_nano"]
        assert reserved >= 0
        peak_obligation = max(peak_obligation, spent + reserved)
    assert reserved == 0 and peak_obligation <= auth["max_estimated_nano"] and len(calls) <= auth["max_http_requests"]
    for call in calls:
        assert call["run_id"] in run_ids
        assert call["status_code"] == 200 and call["error_type"] is None
        assert call["complete"] and call["usage_complete"]
        assert call["request"]["model"] == "deepseek-v4-flash"
        assert call["response_model"] == call["response"]["model"] == "deepseek-flash"
        assert json.loads(call["raw_response"]) == call["response"]
        # Exact observed request encoding: UTF-8 JSON with default comma/colon spacing.
        encoded = json.dumps(call["request"], ensure_ascii=False, allow_nan=False).encode("utf-8")
        assert hashlib.sha256(encoded).hexdigest() == call["request_sha256"]
        assert all(value == call[key] for key, value in finishes[call["id"]].items() if key != "event")
        for key in ["task_id", "run_id", "request", "request_sha256", "reserved_nano"]:
            assert starts[call["id"]][key] == call[key]
    task_users = {t["id"]: t["user_id"] for t in tasks}
    assert Counter((task_users[c["task_id"]], c["request"]["model"], c["reserved_nano"], c["input_tokens"], c["output_tokens"], c["cost_nano"]) for c in calls) == Counter(
        (c["user_id"], c["model"], c["reserved_nano"], c["prompt_tokens"], c["completion_tokens"], c["actual_nano"]) for c in sql["charges"])
    assert Counter((c["task_id"], c["reserved_nano"], c["input_tokens"], c["output_tokens"], c["cost_nano"]) for c in calls) == Counter(
        (c["task_id"], c["reserved_nano"], c["prompt_tokens"], c["completion_tokens"], c["actual_nano"]) for c in ledger["charges"])
    assert all(c["status"] == "settled" for c in sql["charges"] + ledger["charges"])
    assert all(c["pricing_version"] == wire_freeze["pricing_version"] for c in sql["charges"])
    assert sql["budgets"] == {"spent_nano": spent, "reserved_nano": 0, "blocked_users": 0}
    assert spent == 301466000
    assert sql["run_statuses"] == {"completed": 10} and sql["pending_runs"] == ledger["pending_runs"] == 0
    assert sql["meal_rows"] == sql["proposal_rows"] == 0 and sql["plans"] == 8 and sql["sessions"] == 5
    assert not any(sql[k] for k in ["failed_runs", "failed_run_tools", "failed_run_messages"])

    model_keys = [value.encode("utf-8") for name, value in dotenv_values(ROOT / ".env").items()
                  if name in {"DEEPSEEK_API_KEY", "EMBEDDING_API_KEY"} and value]
    assert model_keys
    for path in OUT.rglob("*"):
        if path.is_file():
            assert not any(key in path.read_bytes() for key in model_keys), "Model credential found in artifact"
    metrics = summarize(tasks, calls, reviews)
    write("metrics-reviewed.json", metrics)
    validation = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(), "candidate": candidate,
        "scope": "Eight preregistered, implementer-authored synthetic regressions; real model via local TCP API and worker; manual, non-blind answer review.",
        "verdict": "all_8_preregistered_tasks_completed", "cases": case_results,
        "source_files_verified": len(freeze["source_files"]), "harness_and_shared_observer_unchanged": True,
        "source_clean": True, "preflight_fixture_passed": True, "model_credential_absence_checked": True,
        "http_calls": len(calls), "http_errors": 0, "unknown_usage": 0, "pending_runs": 0,
        "model_ids": {"requested": "deepseek-v4-flash", "provider_returned": "deepseek-flash", "calls": len(calls)},
        "cost_nano": spent, "estimated_cost_cny": spent / 1e9, "reserved_nano": reserved,
        "peak_spent_plus_reserved_nano": peak_obligation,
        "cost_basis": "Application conservative peak/cache-miss tariff; not a provider invoice.",
        "ledger_and_sql_reconciled": True, "all_business_snapshots_unchanged": True,
        "invalid_visible_timings": invalid_timings, "timing_exclusion_rule_ms": 50,
        "known_nonblocking_observations": ["I01 history answer also includes unrequested set/repetition/volume summaries. This is a brevity issue outside the two registered inference boundaries."],
        "limits": ["Each scenario executed once; no retries selected for a passing answer.",
                   "Three clock disagreements are preserved with null visible timing; no replacement timing or rerun.",
                   "Passing samples do not establish population reliability, general coaching quality or production/phone latency.",
                   "Route 33 and meal 31 x 2 release suites were not rerun on 3916629; old 2f8eca1 scores are not transferred.",
                   "No application changes, main-workspace integration, merge, deployment or new phone samples in this run."]}
    write("validation.json", validation)
    print(json.dumps({"tasks": 8, "turns": 10, "http_calls": len(calls), "estimated_cny": spent / 1e9,
                      "invalid_timings": len(invalid_timings), "reconciled": True}))


if __name__ == "__main__":
    main()
