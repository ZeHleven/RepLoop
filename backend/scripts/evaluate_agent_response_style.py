"""Bounded real-model answer A/B and routing probes using synthetic inputs only.

Not an end-to-end write test or an automatic judge of elegance. Review replies
against each case's rubric; routing probes never execute their proposed tools.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "backend/evals/agent_response_style_cases.json"


def load_cases():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def load_model_environment(env_file: Path):
    from dotenv import dotenv_values
    values = dotenv_values(env_file, interpolate=False)
    base = values.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"
    parsed = urlparse(base)
    if parsed.scheme != "https" or parsed.hostname != "api.deepseek.com" or parsed.username or parsed.query:
        raise ValueError("Unreviewed model endpoint")
    if not values.get("DEEPSEEK_API_KEY"):
        raise ValueError("Missing model credential")
    for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_CHAT_MODEL",
                 "DEEPSEEK_REASONING_MODEL", "AGENT_MODEL", "AGENT_INTENT_MODEL"):
        if values.get(name):
            os.environ[name] = values[name]
    # Never inherit or copy database credentials. This runner does not use DB I/O.
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://unused:unused@127.0.0.1:1/style_no_database"
    os.environ["SECRET_KEY"] = "synthetic-offline-eval-no-auth"
    os.environ["AGENT_INTENT_MODEL_ENABLED"] = "true"
    os.environ["AGENT_RULES_FIRST_ENABLED"] = "false"


def baseline_prompt(ref: str, stage: str):
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("Baseline must be an immutable full commit SHA")
    filename, name = ("agent_planner.py", "FINALIZER_SYSTEM_PROMPT") if stage == "finalizer" else ("agent_runtime.py", "SYSTEM_PROMPT")
    source = subprocess.check_output(
        ["git", "show", f"{ref}:backend/app/services/{filename}"], cwd=ROOT,
    ).decode("utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Baseline prompt missing")


def style_flags(reply: str, max_chars: int):
    # Triage signals, not a semantic or medical safety grade.
    return [label for label, present in (
        ("empty_reply", not reply.strip()),
        ("long_for_case", len(reply) > max_chars),
        ("heading_heavy", len(re.findall(r"(?m)^#{1,6}\s", reply)) > 1),
        ("technical_leak", bool(re.search(r"Traceback|semantic_mutation_structure|missing_slots|terminal_action", reply))),
    ) if present]


async def evaluate(case, prompt):
    from app.schemas.agent_planning import FinalizationDecision
    from app.services.agent_planner import _invoke_structured
    from app.services.agent_runtime import _build_model
    model = _build_model(temperature=0, max_tokens=900)
    started = time.perf_counter()
    if case.get("stage") == "finalizer":
        result = await _invoke_structured(
            model, FinalizationDecision, system_prompt=prompt,
            payload={"goal": case["message"], "step_results": [],
                     "tool_observations": case.get("observations", []),
                     "allowed_outcomes": ["informational_answer", "insufficient_evidence"]},
            stage="finalizer",
        )
        reply = result.parsed.reply
        usage = {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens}
        outcome = result.parsed.outcome
    else:
        result = await model.ainvoke([
            {"role": "system", "content": prompt}, *case.get("history", []),
            {"role": "user", "content": case["message"]},
        ])
        reply = result.content
        if not isinstance(reply, str):
            raise ValueError("Non-text reply")
        usage = result.usage_metadata
        outcome = None
    return {"reply": reply, "chars": len(reply), "usage": usage, "outcome": outcome,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "flags": style_flags(reply, case["max_chars"])}


async def run(args):
    from app.config import settings
    from app.services.agent_runtime import SYSTEM_PROMPT
    from app.services.agent_planner import FINALIZER_SYSTEM_PROMPT
    from app.services.agent_intent import route_tools, parse_explicit_proposal_decision
    from app.services.agent_intent_model import resolve_intent_with_fallback
    cases = load_cases()
    if args.ids:
        requested = set(args.ids.split(","))
        if requested - {case["id"] for case in cases}:
            raise ValueError("Unknown case ID")
        cases = [case for case in cases if case["id"] in requested]
    semaphore = asyncio.Semaphore(2)

    async def one(case):
        async with semaphore:
            row = {"id": case["id"], "split": case["split"], "review": case["review"], "message": case["message"], "samples": {}}
            stage = case.get("stage", "direct")
            prompts = {"candidate": FINALIZER_SYSTEM_PROMPT if stage == "finalizer" else SYSTEM_PROMPT}
            if args.baseline_ref:
                prompts = {"baseline": baseline_prompt(args.baseline_ref, stage), **prompts}
            # Alternate A/B call order to reduce a consistent ordering bias.
            for label, prompt in sorted(prompts.items(), reverse=len(case["id"]) % 2 == 0):
                try:
                    row["samples"][label] = await asyncio.wait_for(evaluate(case, prompt), 60)
                except Exception as exc:
                    row["samples"][label] = {"error_type": type(exc).__name__}
                row["samples"][label]["prompt_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
            if args.routing and case.get("route_read_only"):
                try:
                    result = await asyncio.wait_for(resolve_intent_with_fallback(
                        case["message"], context_messages=case.get("history", []), use_model=True,
                    ), 45)
                    resolution = result.resolution
                    row["routing"] = {"source": result.source, "failed": result.understanding_failed,
                        "error_category": result.error_category, "request_kind": resolution.request_kind,
                        "effect": resolution.requested_effect,
                        "clarification": resolution.clarification_question,
                        "allowed_tools": route_tools(resolution),
                        "semantic_read_only": not result.understanding_failed and not resolution.change_requests and resolution.requested_effect == "read",
                        "safe_stop_required": result.understanding_failed,
                        "decision_guard_required": resolution.requested_effect == "decide" and parse_explicit_proposal_decision(case['message']) is None}
                except Exception as exc:
                    row["routing"] = {"error_type": type(exc).__name__}
            print(f"Finished synthetic case: {case['id']}", flush=True)
            return row

    rows = await asyncio.gather(*(one(case) for case in cases))
    return {"model": settings.AGENT_MODEL, "intent_model": settings.AGENT_INTENT_MODEL,
            "temperature": 0, "baseline_ref": args.baseline_ref,
            "dataset_sha256": hashlib.sha256(DATASET.read_bytes()).hexdigest(),
            "scope": "answer-stage A/B; optional routing-only probes; no business writes or production data",
            "human_review_required": True, "cases": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-ref")
    parser.add_argument("--routing", action="store_true")
    parser.add_argument("--ids", help="Optional comma-separated case IDs")
    args = parser.parse_args()
    load_model_environment(args.env_file)
    sys.path.insert(0, str(ROOT / "backend"))
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
