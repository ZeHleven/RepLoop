"""Complete multi-turn API/SQL acceptance; raw evidence survives failures."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from evals.postgres_test_fixtures import query_eval_database_url
from evaluate_agent_tasks import source_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=["scripted", "live"], default="scripted")
    args = parser.parse_args()
    query_eval_database_url()
    if args.output.exists():
        parser.error("Choose a new output directory; previous attempts are immutable")
    args.output.mkdir(parents=True)
    environment = dict(os.environ, MULTITURN_EVAL_DIR=str(args.output.resolve()), MULTITURN_EVAL_MODE=args.mode,
                       PYTHONPATH=str(BACKEND), PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    if args.mode == "scripted":
        environment["DEEPSEEK_API_KEY"] = ""
    command = [sys.executable,"-m","pytest","--noconftest","-p","evals.task_fixtures","-p","no:cacheprovider",
               str(BACKEND / "evals/tests/test_multiturn_tasks.py"),"-q","--tb=short"]
    with (args.output / "pytest.log").open("w",encoding="utf-8") as stream:
        try:
            code = subprocess.run(command,env=environment,stdout=stream,stderr=subprocess.STDOUT,timeout=600).returncode
        except subprocess.TimeoutExpired:
            code=124
    cases=[]
    for case_id in ("meal_confirm","meal_reject","meal_cancel","query_range","plan_revision"):
        path=args.output / f"{case_id}.json"
        row=json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        cases.append({"id":case_id,"passed":row.get("passed") is True and row.get("model_mode")==args.mode})
    report={"cases":cases,"passed_gate":code==0 and all(row["passed"] for row in cases),
            "pytest_exit_code":code,"mode":args.mode,"source":source_fingerprint(),
            "scope":"5 multi-turn query/plan/meal tasks plus SQL state isolation; synthetic data, no UI completion claim",
            "created_at":datetime.now(timezone.utc).isoformat()}
    (args.output / "report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    (args.output / "failure-inbox.json").write_text(json.dumps([dict(row,classification="untriaged") for row in cases if not row["passed"]],ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"passed_gate":report["passed_gate"],"cases":cases}))
    return 0 if report["passed_gate"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
