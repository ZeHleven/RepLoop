from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.agent_intent import (  # noqa: E402
    normalize_resolution,
    resolve_intent,
    resolve_pending_clarification,
    route_tools,
)
from app.services.agent_intent_model import resolve_intent_with_fallback  # noqa: E402


DEFAULT_CASE_FILE = Path(__file__).resolve().parents[1] / "evals" / "agent_intent_cases.json"


async def evaluate(case_file: Path, *, use_model: bool) -> dict[str, Any]:
    all_cases = json.loads(case_file.read_text(encoding="utf-8"))
    cases = [
        case for case in all_cases
        if use_model or not case.get("model_only", False)
    ]
    results: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    primary_correct = 0
    risk_correct = 0
    expected_allowed = 0
    allowed_matched = 0
    forbidden_total = 0
    forbidden_leaks = 0
    required_evidence_total = 0
    required_evidence_matched = 0
    forbidden_evidence_total = 0
    forbidden_evidence_leaks = 0
    generation_cases = 0
    generation_as_mutation = 0
    clarification_correct = 0
    semantic_expected = 0
    semantic_correct = 0
    slot_expected = 0
    slot_correct = 0
    non_clarification_cases = 0
    incorrect_clarifications = 0

    for case in cases:
        if use_model:
            outcome = await resolve_intent_with_fallback(
                case["message"],
                context_messages=case.get("context_messages"),
                pending_clarification=case.get("pending_clarification"),
                use_model=True,
            )
            resolution = outcome.resolution
            source = outcome.source
            fallback_reason = outcome.fallback_reason
        else:
            pending = resolve_pending_clarification(
                case["message"], case.get("pending_clarification")
            )
            if pending is not None:
                pending_resolution, fallback_reason = pending
                resolution = normalize_resolution(
                    case["message"], pending_resolution
                )
            else:
                resolution = resolve_intent(case["message"])
                fallback_reason = "deterministic_evaluation"
            if resolution.request_kind == "mutation":
                # Rules are evaluated for their read/write safety signal only.
                # Production never promotes regex-extracted write fields or
                # persists rule-authored clarification slots.
                resolution = resolution.model_copy(update={
                    "change_requests": [],
                    "evidence_requirements": [],
                    "missing_slots": [],
                    "clarification_required": False,
                    "clarification_question": None,
                })
            source = "rules"
        routed = route_tools(resolution)
        routed_set = set(routed)
        rules_mutation = (
            not use_model
            and case.get("expected_request_kind") == "mutation"
        )
        allowed = set([] if rules_mutation else case["allowed_tools"])
        forbidden = set(case["forbidden_tools"])
        evidence = set(resolution.evidence_requirements)
        required_evidence = set(case.get("required_evidence", []))
        forbidden_evidence = set(case.get("forbidden_evidence", []))
        primary_ok = resolution.primary_intent == case["expected_primary"]
        risk_ok = resolution.risk_level == case["risk_level"]
        allowed_hits = len(allowed & routed_set)
        leaks = sorted(forbidden & routed_set)
        expected_clarification = (
            False
            if rules_mutation
            else case.get("clarification_required", False)
        )
        clarification_ok = (
            resolution.clarification_required == expected_clarification
        )
        semantic_checks: list[bool] = []
        if "expected_domain" in case:
            semantic_checks.append(
                resolution.intent_domain == case["expected_domain"]
            )
        if "expected_request_kind" in case:
            semantic_checks.append(
                resolution.request_kind == case["expected_request_kind"]
            )
        if "expected_effect" in case:
            semantic_checks.append(
                resolution.requested_effect == case["expected_effect"]
            )
        if "expected_requested_output" in case:
            semantic_checks.append(
                resolution.requested_output
                == case["expected_requested_output"]
            )
        if required_evidence:
            semantic_checks.append(required_evidence <= evidence)
        if forbidden_evidence:
            semantic_checks.append(not (forbidden_evidence & evidence))
        slot_ok: bool | None = None
        if "expected_change_field" in case and not rules_mutation:
            slot_ok = (
                len(resolution.change_requests) == 1
                and resolution.change_requests[0].field_path
                == case["expected_change_field"]
                and resolution.change_requests[0].value
                == case.get("expected_change_value")
            )
            semantic_checks.append(slot_ok)
        if "expected_missing_slots" in case and not rules_mutation:
            missing_ok = (
                resolution.missing_slots == case["expected_missing_slots"]
            )
            slot_ok = missing_ok if slot_ok is None else slot_ok and missing_ok
            semantic_checks.append(missing_ok)
        semantic_ok = all(semantic_checks)

        primary_correct += int(primary_ok)
        risk_correct += int(risk_ok)
        expected_allowed += len(allowed)
        allowed_matched += allowed_hits
        forbidden_total += len(forbidden)
        forbidden_leaks += len(leaks)
        required_evidence_total += len(required_evidence)
        required_evidence_matched += len(required_evidence & evidence)
        forbidden_evidence_total += len(forbidden_evidence)
        forbidden_evidence_leaks += len(forbidden_evidence & evidence)
        if case.get("expected_request_kind") == "generation":
            generation_cases += 1
            generation_as_mutation += int(resolution.request_kind == "mutation")
        clarification_correct += int(clarification_ok)
        if not expected_clarification:
            non_clarification_cases += 1
            incorrect_clarifications += int(
                resolution.clarification_required
            )
        if slot_ok is not None:
            slot_expected += 1
            slot_correct += int(slot_ok)
        semantic_expected += int(bool(semantic_checks))
        semantic_correct += int(bool(semantic_checks) and semantic_ok)
        source_counts[source] += 1
        results.append({
            "id": case["id"],
            "primary": resolution.primary_intent,
            "expected_primary": case["expected_primary"],
            "primary_ok": primary_ok,
            "risk_level": resolution.risk_level,
            "risk_ok": risk_ok,
            "resolved_query": resolution.resolved_query,
            "references": [item.model_dump() for item in resolution.references],
            "subtasks": resolution.subtasks,
            "clarification_required": resolution.clarification_required,
            "expected_clarification": expected_clarification,
            "clarification_ok": clarification_ok,
            "intent_domain": resolution.intent_domain,
            "request_kind": resolution.request_kind,
            "requested_effect": resolution.requested_effect,
            "requested_output": resolution.requested_output,
            "evidence_requirements": resolution.evidence_requirements,
            "change_requests": [
                item.model_dump(mode="json")
                for item in resolution.change_requests
            ],
            "semantic_ok": semantic_ok,
            "tools": routed,
            "missing_allowed_tools": sorted(allowed - routed_set),
            "forbidden_tool_leaks": leaks,
            "source": source,
            "fallback_reason": fallback_reason,
        })

    total = len(cases)
    return {
        "mode": "model" if use_model else "rules",
        "case_count": total,
        "primary_accuracy": primary_correct / total if total else 0,
        "risk_accuracy": risk_correct / total if total else 0,
        "clarification_accuracy": (
            clarification_correct / total if total else 0
        ),
        "semantic_accuracy": (
            semantic_correct / semantic_expected if semantic_expected else 1
        ),
        "slot_match_rate": (
            slot_correct / slot_expected if slot_expected else 1
        ),
        "incorrect_clarification_rate": (
            incorrect_clarifications / non_clarification_cases
            if non_clarification_cases else 0
        ),
        "allowed_tool_recall": (
            allowed_matched / expected_allowed if expected_allowed else 1
        ),
        "forbidden_tool_leakage": (
            forbidden_leaks / forbidden_total if forbidden_total else 0
        ),
        "required_evidence_coverage": (
            required_evidence_matched / required_evidence_total
            if required_evidence_total else 1
        ),
        "irrelevant_evidence_rate": (
            forbidden_evidence_leaks / forbidden_evidence_total
            if forbidden_evidence_total else 0
        ),
        "generation_as_mutation_rate": (
            generation_as_mutation / generation_cases
            if generation_cases else 0
        ),
        "source_counts": dict(source_counts),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Agent intent and tool routing")
    parser.add_argument("--mode", choices=("rules", "model"), default="rules")
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mode == "model" and not settings.DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY is required for --mode model", file=sys.stderr)
        return 2
    report = asyncio.run(evaluate(args.case_file, use_model=args.mode == "model"))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and (
        report["primary_accuracy"] < 0.85
        or report["allowed_tool_recall"] < 0.9
        or report["forbidden_tool_leakage"] > 0
        or report["risk_accuracy"] < 1
        or report["clarification_accuracy"] < 1
        or report["semantic_accuracy"] < 1
        or report["required_evidence_coverage"] < 1
        or report["irrelevant_evidence_rate"] > 0
        or report["generation_as_mutation_rate"] > 0
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
