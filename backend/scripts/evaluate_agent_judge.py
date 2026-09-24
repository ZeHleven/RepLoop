"""Run production Agent reasoning against synthetic tools and a live LLM judge."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
load_dotenv(ROOT / ".env", override=False)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-file", type=Path, default=BACKEND / "evals/agent_judge_cases.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--judge-model", default=os.getenv("AGENT_JUDGE_MODEL"))
    parser.add_argument("--case-id", action="append", help="Select a case; repeat to select several")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=80)
    parser.add_argument("--candidate-timeout", type=float, default=120)
    parser.add_argument("--judge-timeout", type=float, default=90)
    parser.add_argument("--validate-only", action="store_true", help="No model requests or database access")
    parser.add_argument("--judge-sanity-check", action="store_true", help="Judge planted answers; does not run the Agent")
    parser.add_argument("--strict", action="store_true", help="Exit nonzero on quality failure or incomplete grading")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 5 or not 0 <= args.threshold <= 100:
        parser.error("repeat must be 1..5 and threshold 0..100")
    if args.candidate_timeout <= 0 or args.judge_timeout <= 0:
        parser.error("timeouts must be positive")
    return args


async def run(args) -> int:
    from langchain_openai import ChatOpenAI
    from app.config import settings
    from evals.judge_eval import (
        Judgment, call_judge, check_judge_controls, judge_payload, load_cases, new_report,
        run_candidate, score_judgment, write_reports,
    )
    cases = load_cases(args.case_file)
    if args.case_id:
        unknown = set(args.case_id) - {case.id for case in cases}
        if unknown:
            print("Unknown case IDs: " + ", ".join(sorted(unknown)), file=sys.stderr)
            return 2
        cases = [case for case in cases if case.id in args.case_id]
    if args.validate_only:
        print(f"Validated {len(cases)} cases. No model calls or database access.")
        return 0

    if not settings.DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY is required for the candidate Agent.", file=sys.stderr)
        return 2
    judge_key = os.getenv("AGENT_JUDGE_API_KEY")
    judge_url = os.getenv("AGENT_JUDGE_BASE_URL")
    # A different endpoint must never silently receive the candidate provider key.
    if bool(judge_key) != bool(judge_url):
        print("Set AGENT_JUDGE_API_KEY and AGENT_JUDGE_BASE_URL together.", file=sys.stderr)
        return 2
    judge_model = args.judge_model or settings.DEEPSEEK_REASONING_MODEL
    if judge_url and not args.judge_model:
        print("Specify --judge-model for an alternative Judge provider.", file=sys.stderr)
        return 2
    model = ChatOpenAI(
        model=judge_model, api_key=judge_key or settings.DEEPSEEK_API_KEY,
        base_url=judge_url or settings.DEEPSEEK_BASE_URL,
        temperature=0, max_tokens=2400, timeout=args.judge_timeout,
        max_retries=0, use_responses_api=False,
    )
    output = args.output or ROOT / "artifacts/agent-evals" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if args.judge_sanity_check:
        controls = await check_judge_controls(model, cases, output,
                                               model_name=judge_model, timeout=args.judge_timeout)
        return 0 if controls["matched_count"] == len(controls["results"]) else 1
    if (output / "report.json").exists():
        print("Output already contains report.json; choose a new directory.", file=sys.stderr)
        return 2
    report = new_report(candidate_model=settings.AGENT_MODEL, judge_model=judge_model,
                        threshold=args.threshold, case_file=args.case_file,
                        planned_samples=len(cases) * args.repeat)
    report["intent_model"] = settings.AGENT_INTENT_MODEL
    report["judge_provider"] = "alternative" if judge_url else "candidate_provider"
    write_reports(output, report)
    print(f"Report directory: {output}", flush=True)
    for repeat in range(args.repeat):
        for case in cases:
            sample_id = f"{case.id}#{repeat + 1}"
            print(f"Running {sample_id}", flush=True)
            candidate = await run_candidate(case, timeout=args.candidate_timeout)
            row = {"sample_id": sample_id, "case_id": case.id, "category": case.category,
                   "case": case.model_dump(mode='json'), "candidate": candidate}
            if candidate["status"] != "ok":
                row["status"] = "candidate_error"
            else:
                judged = await call_judge(model, judge_payload(case, candidate), timeout=args.judge_timeout)
                row["judge"] = judged
                if judged["status"] != "ok":
                    row["status"] = "judge_error"
                else:
                    row["status"] = "evaluated"
                    row.update(score_judgment(Judgment.model_validate(judged["judgment"]),
                                              candidate["deterministic_violations"], args.threshold))
            report["results"].append(row)
            write_reports(output, report)
            print(f"  {row['status']} score={row.get('score', 'n/a')} passed={row.get('passed', 'n/a')}", flush=True)
    report["status"] = "completed"
    write_reports(output, report)
    summary = report["summary"]
    print(f"Judged {summary['judged_count']}/{summary['sample_count']}; passed {summary['pass_count']}.")
    if summary["candidate_error_count"] or summary["judge_error_count"]:
        return 2
    return 1 if args.strict and summary["pass_count"] != summary["sample_count"] else 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted; completed samples remain in the report.", file=sys.stderr)
        return 130
    except Exception as exc:
        # Configuration/provider exceptions may contain credentials; print only type.
        print(f"Evaluation setup failed: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
