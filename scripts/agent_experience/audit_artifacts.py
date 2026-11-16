"""Recompute evidence coverage without sending requests or changing raw samples."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/agent-dev-notes/current-experience-metrics-20261108"
SOURCE = ROOT / ".tmp/agent-experience-20261108-src"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    raw = read(BASE / "live-02/result.json")
    freeze = read(BASE / "live-02/freeze.json")
    ledger = read(BASE / "live-02/ledger.json")
    persistence = read(BASE / "live-02/persistence-verification.json")
    assert subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip() == raw["source_commit"]
    assert not subprocess.check_output(["git", "-C", str(SOURCE), "status", "--porcelain"], text=True).strip()
    assert all(digest(SOURCE / p) == sha for p, sha in freeze["source_files"].items())
    frozen_observer = BASE / "frozen-observer"
    frozen_observer.mkdir(exist_ok=True)
    for name, sha in freeze["observer_files"].items():
        path = Path(__file__).parent / name
        assert digest(path) == sha, name
        target = frozen_observer / name
        if not target.exists():
            shutil.copyfile(path, target)
        assert digest(target) == sha
    evidence_source = BASE / "source-evidence"
    evidence_source.mkdir(exist_ok=True)
    source_names = ("agent_intent_model.py", "agent_query_reports.py", "workout_history_reporting.py", "agent_tools.py")
    source_copies = {}
    for name in source_names:
        source = SOURCE / "backend/app/services" / name
        target = evidence_source / name
        if not target.exists():
            shutil.copyfile(source, target)
        assert digest(source) == digest(target)
        source_copies[name] = digest(target)
    calls = raw["provider_calls"]
    runs = {turn["run_id"]: task["id"] for task in raw["tasks"] for turn in task["turns"]}
    assert len(runs) == 22 and len(calls) == 70 and not raw["stopped"]
    assert len({c["id"] for c in calls}) == len(calls)
    assert all(runs[c["run_id"]] == c["task_id"] and c["worker_attempt"] == 1 for c in calls)
    assert all(c["complete"] and c["usage_complete"] and c["status_code"] == 200 and c["error_type"] is None for c in calls)
    events = [json.loads(line) for line in (BASE / "live-02/provider-events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert Counter((e["id"], e["event"]) for e in events) == Counter((c["id"], phase) for c in calls for phase in ("start", "finish"))
    observed, charged = defaultdict(int), defaultdict(int)
    for c in calls:
        observed[c["task_id"]] += c["cost_nano"]
    for c in ledger["charges"]:
        assert c["status"] == "settled" and c["actual_nano"] is not None
        charged[c["task_id"]] += c["actual_nano"]
    assert observed == charged and len(ledger["charges"]) == 70 and ledger["pending_runs"] == 0
    assert persistence["pending_runs"] == 0
    for task in raw["tasks"]:
        assert task["collection_complete"]
        # Planned exercise and meal rows must stay unchanged until the explicit confirmation.
        for turn in task["turns"]:
            for domain in ("plan", "meals"):
                assert turn["business_after"][domain] == task["before"][domain], (task["id"], domain)
        if task["case_id"] not in ("W01", "W02"):
            assert task["after"] == task["before"], task["id"]
        else:
            assert task["confirmation"]["status"] == 200 and task["confirmation"]["response"]["applied"] is True
    # Check known secrets by value without logging or persisting them.
    key_values = {dotenv_values(ROOT / name).get("DEEPSEEK_API_KEY") for name in (".env", ".env.production")}
    key_values.discard(None)
    key_values.discard("")
    checked_files = 0
    for path in BASE.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".jsonl", ".log", ".md", ".py"):
            data = path.read_text(encoding="utf-8")
            assert not any(key in data for key in key_values), "secret_in_artifact"
            checked_files += 1
    previous = read(BASE / "live-01/result.json")
    assert len(previous["provider_calls"]) == 2
    assert all(c["status_code"] == 401 and not c["usage_complete"] for c in previous["provider_calls"])
    reserved = sum(c["reserved_nano"] for c in previous["provider_calls"])
    known = sum(observed.values())
    assert known + reserved <= 5_000_000_000
    turns = [turn for task in raw["tasks"] for turn in task["turns"]]
    report = {
        "source_commit": raw["source_commit"], "product_files_unchanged": len(freeze["source_files"]),
        "frozen_observer_files": len(freeze["observer_files"]), "source_evidence_sha256": source_copies,
        "task_count": len(raw["tasks"]), "run_statuses": dict(Counter(t["response"]["status"] for t in turns)),
        "provider_attempts_attributed": len(calls), "ledger_charges": len(ledger["charges"]), "ledger_matches_per_task": True,
        "pending_runs": ledger["pending_runs"], "early_plan_or_meal_writes": 0,
        "valid_visible_timings": sum(t["visible_timing"]["ms"] is not None for t in turns),
        "invalid_visible_timings": [{"task": t["id"], "turn": i, **v["visible_timing"]}
            for t in raw["tasks"] for i, v in enumerate(t["turns"]) if v["visible_timing"]["ms"] is None],
        "known_model_keys_absent": True, "secret_scan_files": checked_files,
        "known_input_tokens": sum(c["input_tokens"] for c in calls), "known_output_tokens": sum(c["output_tokens"] for c in calls),
        "budget": {"limit_cny": 5, "live_02_known_estimate_cny": known / 1e9,
            "live_01_unknown_usage_reserved_cny": reserved / 1e9,
            "known_plus_reserve_cny": (known + reserved) / 1e9, "live_attempts_all_batches": len(calls) + 2,
            "actual_provider_invoice_cny": None, "basis": freeze["pricing_version"]},
        "deployment_zip_sha256": digest(ROOT / "deploy/cloudbase/fitness-agent-backend-0.5.47-51feceb.zip"),
    }
    (BASE / "audit.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("product_files_unchanged", "task_count", "run_statuses", "provider_attempts_attributed", "valid_visible_timings", "budget")}))


if __name__ == "__main__":
    main()
