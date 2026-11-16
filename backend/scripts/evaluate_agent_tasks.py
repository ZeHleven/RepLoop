"""Run complete synthetic tasks against an explicitly isolated local PostgreSQL."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from evals.postgres_test_fixtures import query_eval_database_url
from evals.task_contracts import DATASET, build_report, load_tasks


def source_fingerprint() -> dict:
    digest = hashlib.sha256()
    for directory in ("app", "evals", "scripts"):
        for path in sorted((BACKEND / directory).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".json"}:
                digest.update(path.relative_to(BACKEND).as_posix().encode() + b"\0")
                digest.update(path.read_bytes() + b"\0")
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND.parent,
                                  capture_output=True, text=True)
        head = revision.stdout.strip() if revision.returncode == 0 else None
    except OSError:
        head = None  # container may mount source only, without Git
    return {"git_head": head,
            "backend_source_sha256": digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["development", "holdout", "all"], default="development")
    parser.add_argument("--mode", choices=["scripted", "live"], default="scripted")
    parser.add_argument("--cases", help="Comma-separated IDs; unknown or unsupported IDs fail.")
    parser.add_argument("--output", type=Path, required=True, help="New directory; never overwrite a run.")
    parser.add_argument("--replay-from", type=Path, help="Re-score saved raw records without DB or model calls.")
    args = parser.parse_args()
    if not args.replay_from:
        query_eval_database_url()  # refuse nonlocal/production-looking DBs before pytest
    dataset = load_tasks()
    cases = [case for case in dataset.cases if args.split == "all" or case.split == args.split]
    if args.cases:
        requested = set(args.cases.split(","))
        selected = {case.id for case in cases} & requested
        if requested != selected:
            parser.error("Unknown IDs or IDs outside the selected split")
        cases = [case for case in cases if case.id in selected]
    if not cases:
        parser.error("No tasks selected")
    if args.mode == "live" and any(not case.live_supported for case in cases):
        parser.error("Select only live_supported cases; fault injection cases require scripted mode")
    if args.output.exists():
        parser.error("Output directory already exists; retain it and choose a new run directory")
    args.output.mkdir(parents=True)
    env = dict(os.environ, AGENT_TASK_EVAL_DIR=str(args.output.resolve()),
               AGENT_TASK_EVAL_MODE=args.mode,
               AGENT_TASK_EVAL_CASES=",".join(case.id for case in cases),
               PYTHONPATH=str(BACKEND), PYTHONIOENCODING="utf-8")
    if args.mode == "scripted":
        env["DEEPSEEK_API_KEY"] = ""
    command = [sys.executable, "-m", "pytest", "--noconftest", "-p", "evals.task_fixtures",
               "-p", "no:cacheprovider", str(BACKEND / "evals/tests/test_agent_tasks.py"),
               "-q", "--tb=short"]
    if args.replay_from:
        exit_code = None
    else:
        with (args.output / "pytest.log").open("w", encoding="utf-8") as stream:
            try:
                exit_code = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                           timeout=60 + 240 * len(cases)).returncode
            except subprocess.TimeoutExpired:
                exit_code = 124
                stream.write("\nTask runner exceeded its wall-clock budget. Incomplete records fail the gate.\n")
    report = build_report(cases, args.replay_from or args.output, expected_model_mode=args.mode)
    report.update({"mode": args.mode, "pytest_exit_code": exit_code,
                   "source": source_fingerprint(),
                   "source_role": "evaluator_replay" if args.replay_from else "execution_and_evaluator",
                   "replay_from": str(args.replay_from) if args.replay_from else None,
                   "created_at": datetime.now(timezone.utc).isoformat(),
                   "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest()})
    report["passed_gate"] = (exit_code == 0 or bool(args.replay_from)) and report["failed"] == 0
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [{"case_id": item["case_id"], "classification": "untriaged",
                 "source": "synthetic_task", "raw_record": os.path.relpath(
                     (args.replay_from or args.output) / (item["case_id"] + ".json"), args.output),
                 "failed_checks": [check for check in item["checks"] if check["status"] != "pass"],
                 "error": item["error"]}
                for item in report["cases"] if not item["passed"]]
    (args.output / "failure-inbox.json").write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("total", "passed", "failed", "mode", "passed_gate")}))
    return 0 if report["passed_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
