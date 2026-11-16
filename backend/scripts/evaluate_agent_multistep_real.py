from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
import time
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import StructuredTool
from langchain_core.tools.base import ToolException


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.agent_controller import execute_planned_agent  # noqa: E402
from app.services.agent_planner import PlanningModelError  # noqa: E402
from app.services.agent_intent import route_tools  # noqa: E402
from app.services.agent_intent_model import (  # noqa: E402
    resolve_intent_with_fallback,
)
from app.services.agent_runtime import (  # noqa: E402
    HIGH_RISK_REPLY,
    SYSTEM_PROMPT,
    _audit_result_summary,
    _build_model,
    _clarification_reply,
    _extract_agent_output,
)
from app.services.agent_tools import build_read_tools  # noqa: E402
from app.services.agent_trace import (  # noqa: E402
    add_stage_timing,
    build_initial_execution_trace,
    complete_execution_trace,
    terminate_execution_trace,
)
from evals.multistep_schema import (  # noqa: E402
    MultistepEvalCase,
    ToolStub,
    load_multistep_dataset,
)
from evals.multistep_scorer import (  # noqa: E402
    score_runtime_execution_trace,
)


def _fixture_coroutine(stub: ToolStub | None):
    async def invoke_fixture(**_arguments: Any) -> dict[str, Any]:
        if stub is None:
            raise ToolException("fixture_not_configured")
        if stub.error is not None:
            if stub.error.retryable or "timeout" in stub.error.code.lower():
                raise TimeoutError(stub.error.code)
            raise ToolException(f"fixture_error:{stub.error.code}")
        return copy.deepcopy(stub.result or {})

    return invoke_fixture


def build_fixture_tools(
    case: MultistepEvalCase,
    allowlist: list[str],
) -> list[StructuredTool]:
    """Reuse production descriptions/schemas but never execute production I/O."""
    templates = build_read_tools(
        None,  # Captured by unused production coroutines only.
        user_id="offline-eval-user",
        allowlist=allowlist,
    )
    stub_by_tool = {stub.tool: stub for stub in case.tool_stubs}
    tools: list[StructuredTool] = []
    for tool_id, template in zip(allowlist, templates, strict=True):
        tools.append(StructuredTool(
            name=template.name,
            description=template.description,
            args_schema=template.args_schema,
            coroutine=_fixture_coroutine(stub_by_tool.get(tool_id)),
            handle_tool_error=False,
        ))
    return tools


def _execution_message(
    case: MultistepEvalCase,
    *,
    resolved_query: str,
    subtasks: list[str],
) -> str:
    message = (
        f"用户原始表达：{case.message}\n"
        f"经服务端对话理解层消解后的请求：{resolved_query}\n"
    )
    if subtasks:
        message += f"本轮需要覆盖的语义目标：{'；'.join(subtasks)}\n"
    return message + "请围绕消解后的请求完成回答，不要扩展到无关目标。"


async def _run_direct(
    case: MultistepEvalCase,
    *,
    history: list[dict[str, str]],
    resolved_query: str,
    subtasks: list[str],
    tool_allowlist: list[str],
    initial_trace,
):
    tools = build_fixture_tools(case, tool_allowlist)
    agent = create_agent(
        model=_build_model(),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        name="fitness_agent_multistep_real_eval",
    )
    started = time.perf_counter()
    result = await agent.ainvoke(
        {
            "messages": [
                *history,
                {
                    "role": "user",
                    "content": _execution_message(
                        case,
                        resolved_query=resolved_query,
                        subtasks=subtasks,
                    ),
                },
            ]
        },
        config={"recursion_limit": settings.AGENT_RECURSION_LIMIT},
    )
    initial_trace = add_stage_timing(
        initial_trace,
        stage="direct_agent",
        source="model",
        status="success",
        latency_ms=round((time.perf_counter() - started) * 1000),
    )
    trace = complete_execution_trace(
        initial_trace,
        result,
        summarize_observation=_audit_result_summary,
    )
    reply, _cards = _extract_agent_output(result)
    return reply, trace


async def evaluate_case(case: MultistepEvalCase) -> dict[str, Any]:
    started = time.perf_counter()
    history = [item.model_dump() for item in case.context_messages]
    intent_outcome = await resolve_intent_with_fallback(
        case.message,
        context_messages=history,
        use_model=True,
    )
    resolution = intent_outcome.resolution
    tool_allowlist = route_tools(resolution)
    trace = build_initial_execution_trace(
        resolution,
        tool_allowlist,
        intent_outcome,
    )

    if trace.execution_mode == "safe_stop":
        reply = HIGH_RISK_REPLY
        trace = terminate_execution_trace(
            trace,
            terminal_action="safe_stop",
            termination_reason="health_red_flag",
        )
    elif trace.execution_mode == "clarify":
        reply = _clarification_reply(resolution)
        trace = terminate_execution_trace(
            trace,
            terminal_action="clarify",
            termination_reason="clarification_required",
        )
    elif trace.execution_mode == "direct":
        reply, trace = await _run_direct(
            case,
            history=history,
            resolved_query=resolution.resolved_query,
            subtasks=resolution.subtasks,
            tool_allowlist=tool_allowlist,
            initial_trace=trace,
        )
    else:
        result = await execute_planned_agent(
            db=None,
            user_id="offline-eval-user",
            run_id=f"real-eval-{case.id}",
            model=_build_model(
                temperature=0,
                max_tokens=settings.AGENT_PLANNING_MAX_TOKENS,
            ),
            goal=resolution.resolved_query,
            subtasks=resolution.subtasks,
            tool_allowlist=tool_allowlist,
            initial_trace=trace,
            summarize_observation=_audit_result_summary,
            tools=build_fixture_tools(case, tool_allowlist),
            proposal_creation_enabled=(
                resolution.intent_domain == "workout_plan"
            ),
        )
        reply = result.reply
        trace = result.execution_trace

    score = score_runtime_execution_trace(case, trace)
    return {
        "id": case.id,
        "title": case.title,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "intent": {
            "source": intent_outcome.source,
            "attempt_count": intent_outcome.attempt_count,
            "latency_ms": intent_outcome.latency_ms,
            "attempt_timings": [
                {
                    "attempt": item.attempt,
                    "latency_ms": item.latency_ms,
                    "status": item.status,
                    "error_category": item.error_category,
                }
                for item in intent_outcome.attempt_timings
            ],
            "fallback_reason": intent_outcome.fallback_reason,
            "error_category": intent_outcome.error_category,
            "primary": resolution.primary_intent,
            "expanded": resolution.expanded_intents,
            "subtasks": resolution.subtasks,
            "risk_level": resolution.risk_level,
            "clarification_required": resolution.clarification_required,
            "tool_allowlist": tool_allowlist,
        },
        "reply": reply,
        "response_requirements": case.expected.response_requirements,
        "trace": trace.model_dump(mode="json"),
        "score": score.model_dump(mode="json"),
    }


async def evaluate(
    cases: list[MultistepEvalCase],
    *,
    timeout_seconds: float,
    repeat: int = 1,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    scheduled = [
        (sample, case)
        for sample in range(1, repeat + 1)
        for case in cases
    ]
    for index, (sample, case) in enumerate(scheduled, start=1):
        print(
            f"[{index}/{len(scheduled)}] sample={sample} real model: {case.id}",
            file=sys.stderr,
            flush=True,
        )
        try:
            result = await asyncio.wait_for(
                evaluate_case(case),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            result = {
                "id": case.id,
                "title": case.title,
                "error": "case_timeout",
            }
        except PlanningModelError as exc:
            result = {
                "id": case.id,
                "title": case.title,
                "error": "planning_model_error",
                "error_stage": exc.stage,
                "error_category": exc.category,
            }
        except Exception as exc:
            result = {
                "id": case.id,
                "title": case.title,
                "error": type(exc).__name__,
            }
        result["sample"] = sample
        results.append(result)

    completed = [item for item in results if "score" in item]
    deterministic_passes = sum(
        bool(item["score"]["deterministic_pass"])
        for item in completed
    )
    hard_gate_passes = sum(
        bool(item["score"]["hard_gate_pass"])
        for item in completed
    )
    total_tool_calls = sum(
        item["trace"]["budget_usage"]["tool_calls"]
        for item in completed
    )
    total_model_calls = sum(
        item["trace"]["budget_usage"]["model_calls"]
        for item in completed
    )
    sample_count = len(scheduled)
    return {
        "mode": "real_model_with_offline_tool_fixtures",
        "model": settings.AGENT_MODEL,
        "case_count": len(cases),
        "repeat_count": repeat,
        "sample_count": sample_count,
        "completed_count": len(completed),
        "deterministic_pass_rate": (
            deterministic_passes / sample_count if sample_count else 0
        ),
        "hard_gate_pass_rate": (
            hard_gate_passes / sample_count if sample_count else 0
        ),
        "total_tool_calls": total_tool_calls,
        "total_runtime_model_calls": total_model_calls,
        "case_latency_summary": _case_latency_summary(results),
        "stage_latency_summary": _stage_latency_summary(completed),
        "three_evidence_fast_path": _three_evidence_fast_path_summary(
            cases,
            results,
        ),
        "failure_summaries": _failure_summaries(results),
        "note": (
            "Intent model calls are not included in trace model_calls. "
            "Response requirements still need human or independent Judge review."
        ),
        "results": results,
    }


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * min(1.0, max(0.0, quantile))
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - lower_index
    interpolated = (
        ordered[lower_index]
        + (ordered[upper_index] - ordered[lower_index]) * fraction
    )
    return round(interpolated)


def _latency_summary(values: list[int]) -> dict[str, int]:
    if not values:
        return {
            "count": 0,
            "mean_ms": 0,
            "p50_ms": 0,
            "p95_ms": 0,
            "min_ms": 0,
            "max_ms": 0,
        }
    return {
        "count": len(values),
        "mean_ms": round(sum(values) / len(values)),
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "min_ms": min(values),
        "max_ms": max(values),
    }


def _case_latency_summary(
    results: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_case.setdefault(result["id"], []).append(result)
    summaries: dict[str, dict[str, Any]] = {}
    for case_id, runs in by_case.items():
        completed = [item for item in runs if "score" in item]
        latencies = [item["latency_ms"] for item in completed]
        summaries[case_id] = {
            "run_count": len(runs),
            "completed_count": len(completed),
            "deterministic_pass_rate": (
                sum(
                    bool(item["score"]["deterministic_pass"])
                    for item in completed
                ) / len(runs)
            ),
            "hard_gate_pass_rate": (
                sum(
                    bool(item["score"]["hard_gate_pass"])
                    for item in completed
                ) / len(runs)
            ),
            **_latency_summary(latencies),
        }
    return summaries


def _stage_latency_summary(
    completed: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    invocation_latencies: dict[str, list[int]] = {}
    per_run_latencies: dict[str, list[int]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for result in completed:
        totals: dict[str, int] = {}
        for timing in result["trace"].get("stage_timings", []):
            stage = timing["stage"]
            latency_ms = timing["latency_ms"]
            invocation_latencies.setdefault(stage, []).append(latency_ms)
            totals[stage] = totals.get(stage, 0) + latency_ms
            stage_metadata = metadata.setdefault(stage, {
                "success_count": 0,
                "error_count": 0,
                "source_counts": {},
            })
            status_key = f'{timing["status"]}_count'
            stage_metadata[status_key] += 1
            source = timing["source"]
            source_counts = stage_metadata["source_counts"]
            source_counts[source] = source_counts.get(source, 0) + 1
        for stage, latency_ms in totals.items():
            per_run_latencies.setdefault(stage, []).append(latency_ms)

    return {
        stage: {
            **metadata[stage],
            "per_invocation": _latency_summary(values),
            "per_run_total": _latency_summary(
                per_run_latencies.get(stage, [])
            ),
        }
        for stage, values in invocation_latencies.items()
    }


def _failure_summaries(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for result in results:
        score = result.get("score")
        if isinstance(score, dict) and score.get("deterministic_pass"):
            continue
        summary: dict[str, Any] = {
            "id": result["id"],
            "sample": result.get("sample"),
        }
        if "error" in result:
            summary.update({
                key: result[key]
                for key in ("error", "error_stage", "error_category")
                if key in result
            })
        if isinstance(score, dict):
            summary["score"] = score
            trace = result.get("trace", {})
            summary["terminal_action"] = trace.get("terminal_action")
            summary["termination_reason"] = trace.get("termination_reason")
            summary["tools"] = [
                {
                    "tool_id": action.get("tool_id"),
                    "status": action.get("status"),
                }
                for action in trace.get("actions", [])
            ]
            summary["plan"] = [
                {
                    "id": step.get("id"),
                    "strategy": step.get("execution_strategy"),
                    "status": step.get("status"),
                    "candidate_tools": step.get("candidate_tools", []),
                }
                for step in trace.get("plan", {}).get("steps", [])
            ]
        failures.append(summary)
    return failures


def _three_evidence_fast_path_summary(
    cases: list[MultistepEvalCase],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    eligible_cases = {
        case.id: case
        for case in cases
        if case.expected.require_three_action_parallel_fast_path
    }
    eligible_results = [
        result for result in results if result["id"] in eligible_cases
    ]

    def classify(result: dict[str, Any]) -> tuple[bool, bool]:
        trace = result.get("trace")
        if not isinstance(trace, dict):
            return False, False
        case = eligible_cases[result["id"]]
        required_tools = {
            group[0] for group in case.expected.required_tool_groups
        }
        steps = trace.get("plan", {}).get("steps", [])
        triple_parallel_hit = any(
            isinstance(step, dict)
            and step.get("execution_strategy") == "parallel_read"
            and len(step.get("planned_actions") or []) == 3
            and {
                action.get("tool_id")
                for action in step.get("planned_actions") or []
                if isinstance(action, dict)
            } == required_tools
            for step in steps
        )
        zero_executor = not any(
            isinstance(timing, dict)
            and timing.get("stage") == "executor"
            for timing in trace.get("stage_timings", [])
        )
        return triple_parallel_hit, zero_executor

    classified = [
        (result, *classify(result)) for result in eligible_results
    ]
    sample_count = len(eligible_results)
    triple_hits = sum(item[1] for item in classified)
    zero_executor_hits = sum(item[2] for item in classified)
    by_case: dict[str, dict[str, Any]] = {}
    for case_id in eligible_cases:
        rows = [item for item in classified if item[0]["id"] == case_id]
        count = len(rows)
        case_triple_hits = sum(item[1] for item in rows)
        case_zero_executor_hits = sum(item[2] for item in rows)
        by_case[case_id] = {
            "sample_count": count,
            "three_action_parallel_hits": case_triple_hits,
            "three_action_parallel_hit_rate": (
                case_triple_hits / count if count else 0.0
            ),
            "zero_executor_hits": case_zero_executor_hits,
            "zero_executor_rate": (
                case_zero_executor_hits / count if count else 0.0
            ),
        }
    return {
        "eligible_case_count": len(eligible_cases),
        "sample_count": sample_count,
        "three_action_parallel_hits": triple_hits,
        "three_action_parallel_hit_rate": (
            triple_hits / sample_count if sample_count else 0.0
        ),
        "zero_executor_hits": zero_executor_hits,
        "zero_executor_rate": (
            zero_executor_hits / sample_count if sample_count else 0.0
        ),
        "by_case": by_case,
    }


def _fast_path_gate_failures(
    report: dict[str, Any],
    *,
    min_parallel_rate: float | None,
    min_zero_executor_rate: float | None,
) -> list[str]:
    metrics = report["three_evidence_fast_path"]
    failures: list[str] = []
    thresholds_requested = (
        min_parallel_rate is not None or min_zero_executor_rate is not None
    )
    if thresholds_requested and metrics["sample_count"] == 0:
        return ["no_three_evidence_fast_path_samples"]
    if (
        min_parallel_rate is not None
        and metrics["three_action_parallel_hit_rate"] < min_parallel_rate
    ):
        failures.append("three_action_parallel_hit_rate_below_threshold")
    if (
        min_zero_executor_rate is not None
        and metrics["zero_executor_rate"] < min_zero_executor_rate
    ):
        failures.append("zero_executor_rate_below_threshold")
    return failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the real Fitness Agent models against deterministic offline "
            "multi-step tool fixtures"
        )
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Case id to run; repeat for a small batch. Defaults to all cases.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=240)
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        choices=range(1, 21),
        metavar="1..20",
        help="Repeat every selected case sequentially for latency sampling.",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--min-three-evidence-parallel-rate",
        type=float,
        choices=[item / 100 for item in range(0, 101)],
        default=None,
        help=(
            "Fail unless marked three-evidence cases use one matching "
            "three-action parallel_read at or above this rate."
        ),
    )
    parser.add_argument(
        "--min-three-evidence-zero-executor-rate",
        type=float,
        choices=[item / 100 for item in range(0, 101)],
        default=None,
        help=(
            "Fail unless marked three-evidence runs have no Executor stage "
            "at or above this rate."
        ),
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print aggregate and per-case summaries without raw run traces.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not settings.DEEPSEEK_API_KEY:
        print("DEEPSEEK_API_KEY is required", file=sys.stderr)
        return 2
    dataset = load_multistep_dataset()
    by_id = {case.id: case for case in dataset.cases}
    unknown = sorted(set(args.case_id) - set(by_id))
    if unknown:
        print(f"Unknown case ids: {unknown}", file=sys.stderr)
        return 2
    cases = (
        [by_id[case_id] for case_id in args.case_id]
        if args.case_id
        else dataset.cases
    )
    report = asyncio.run(evaluate(
        cases,
        timeout_seconds=args.timeout_seconds,
        repeat=args.repeat,
    ))
    printable_report = (
        {key: value for key, value in report.items() if key != "results"}
        if args.summary_only
        else report
    )
    print(json.dumps(printable_report, ensure_ascii=False, indent=2))
    fast_path_gate_failures = _fast_path_gate_failures(
        report,
        min_parallel_rate=args.min_three_evidence_parallel_rate,
        min_zero_executor_rate=(
            args.min_three_evidence_zero_executor_rate
        ),
    )
    if fast_path_gate_failures:
        print(
            "Fast-path gate failed: " + ", ".join(fast_path_gate_failures),
            file=sys.stderr,
        )
    if fast_path_gate_failures or (args.strict and (
        report["completed_count"] != report["sample_count"]
        or report["hard_gate_pass_rate"] < 1
        or report["deterministic_pass_rate"] < 1
    )):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
