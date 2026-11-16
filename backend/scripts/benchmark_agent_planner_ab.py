from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import httpx
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.agent_planner import (  # noqa: E402
    ModelPlanningPolicy,
    _invoke_structured,
    build_tool_catalog,
)
from app.services.agent_tools import build_read_tools  # noqa: E402


class MinimalJSONResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal["ok"]


PLANNER_GOAL = (
    "结合我的资料、当前训练计划和最近四周完成情况，判断计划是否适合我，"
    "必要时给出待确认的调整建议。"
)
PLANNER_SUBTASKS = [
    "读取用户训练资料",
    "读取当前训练计划",
    "读取最近四周训练进度",
]
PLANNER_TOOL_IDS = [
    "profile.get_summary",
    "plan.get_active",
    "workout.get_progress",
]


def _percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    position = (len(ordered) - 1) * percent
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(
        ordered[lower] * (1 - weight) + ordered[upper] * weight,
        1,
    )


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean_ms": round(statistics.fmean(values), 1) if values else None,
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
        "min_ms": round(min(values), 1) if values else None,
        "max_ms": round(max(values), 1) if values else None,
    }


def _model(*, max_retries: int, http_client: httpx.AsyncClient) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.AGENT_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL.rstrip("/"),
        temperature=0,
        timeout=settings.AGENT_TIMEOUT_SECONDS,
        max_retries=max_retries,
        use_responses_api=False,
        max_tokens=settings.AGENT_PLANNING_MAX_TOKENS,
        http_async_client=http_client,
    )


async def _run_sample(
    *,
    arm: str,
    max_retries: int,
    round_number: int,
    deadline_seconds: float,
    tool_catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []

    async def on_request(request: httpx.Request) -> None:
        started = time.perf_counter()
        request.extensions["planner_ab_started"] = started
        attempts.append({
            "request_number": len(attempts) + 1,
            "started": started,
            "headers_ms": None,
            "status_code": None,
        })

    async def on_response(response: httpx.Response) -> None:
        started = response.request.extensions.get("planner_ab_started")
        matching = next(
            (
                item
                for item in reversed(attempts)
                if item["started"] == started
            ),
            None,
        )
        if matching is not None and isinstance(started, float):
            matching["headers_ms"] = round(
                (time.perf_counter() - started) * 1000,
                1,
            )
            matching["status_code"] = response.status_code

    started = time.perf_counter()
    input_tokens: int | None = None
    output_tokens: int | None = None
    status = "success"
    error_type: str | None = None
    async with httpx.AsyncClient(
        event_hooks={"request": [on_request], "response": [on_response]},
    ) as http_client:
        model = _model(max_retries=max_retries, http_client=http_client)
        try:
            if arm == "minimal_json":
                invocation = await asyncio.wait_for(
                    _invoke_structured(
                        model,
                        MinimalJSONResponse,
                        system_prompt=(
                            "Return exactly one JSON object matching "
                            '{"ok":"ok"}. Do not add other fields.'
                        ),
                        payload={"task": "return the required JSON object"},
                        stage="minimal_json",
                        max_payload_chars=1000,
                    ),
                    timeout=deadline_seconds,
                )
                input_tokens = invocation.input_tokens
                output_tokens = invocation.output_tokens
            else:
                policy = ModelPlanningPolicy(model)
                await asyncio.wait_for(
                    policy.create_plan(
                        goal=PLANNER_GOAL,
                        subtasks=PLANNER_SUBTASKS,
                        tool_catalog=tool_catalog,
                    ),
                    timeout=deadline_seconds,
                )
                input_tokens = policy.input_tokens
                output_tokens = policy.output_tokens
        except TimeoutError:
            status = "timeout"
            error_type = "TimeoutError"
        except Exception as exc:  # Diagnostic runner must retain failed samples.
            status = "error"
            error_type = type(exc).__name__

    total_ms = round((time.perf_counter() - started) * 1000, 1)
    safe_attempts = [
        {
            "request_number": item["request_number"],
            "headers_ms": item["headers_ms"],
            "status_code": item["status_code"],
        }
        for item in attempts
    ]
    return {
        "arm": arm,
        "max_retries": max_retries,
        "round": round_number,
        "status": status,
        "error_type": error_type,
        "total_ms": total_ms,
        "http_request_count": len(attempts),
        "http_attempts": safe_attempts,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        groups[(sample["arm"], sample["max_retries"])].append(sample)

    summary: dict[str, Any] = {}
    for (arm, max_retries), items in sorted(groups.items()):
        successful = [item for item in items if item["status"] == "success"]
        header_latencies = [
            attempt["headers_ms"]
            for item in items
            for attempt in item["http_attempts"]
            if isinstance(attempt["headers_ms"], (int, float))
        ]
        summary[f"{arm}__retries_{max_retries}"] = {
            "samples": len(items),
            "successes": len(successful),
            "timeouts": sum(item["status"] == "timeout" for item in items),
            "errors": sum(item["status"] == "error" for item in items),
            "samples_with_retry": sum(
                item["http_request_count"] > 1 for item in items
            ),
            "http_requests": sum(item["http_request_count"] for item in items),
            "total_latency": _latency_summary(
                [item["total_ms"] for item in items]
            ),
            "successful_latency": _latency_summary(
                [item["total_ms"] for item in successful]
            ),
            "response_headers_latency": _latency_summary(header_latencies),
            "successful_input_tokens": [
                item["input_tokens"]
                for item in successful
                if item["input_tokens"] is not None
            ],
            "successful_output_tokens": [
                item["output_tokens"]
                for item in successful
                if item["output_tokens"] is not None
            ],
        }
    return summary


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY is required")
    tools = build_read_tools(
        None,
        user_id="planner-ab-diagnostic",
        allowlist=PLANNER_TOOL_IDS,
    )
    tool_catalog = build_tool_catalog(tools)
    combinations = [
        ("minimal_json", 0),
        ("minimal_json", 1),
        ("real_planner", 0),
        ("real_planner", 1),
    ]
    samples: list[dict[str, Any]] = []
    randomizer = random.Random(args.seed)
    for round_number in range(1, args.repeat + 1):
        round_combinations = combinations.copy()
        randomizer.shuffle(round_combinations)
        for arm, max_retries in round_combinations:
            sample = await _run_sample(
                arm=arm,
                max_retries=max_retries,
                round_number=round_number,
                deadline_seconds=args.deadline_seconds,
                tool_catalog=tool_catalog,
            )
            samples.append(sample)
            print(
                f"{arm} retries={max_retries} round={round_number} "
                f"status={sample['status']} total_ms={sample['total_ms']} "
                f"http_requests={sample['http_request_count']}",
                file=sys.stderr,
                flush=True,
            )
    return {
        "model": settings.AGENT_MODEL,
        "base_url_host": httpx.URL(settings.DEEPSEEK_BASE_URL).host,
        "repeat": args.repeat,
        "deadline_seconds": args.deadline_seconds,
        "seed": args.seed,
        "summary": _summarize(samples),
        "samples": samples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure minimal JSON vs real Planner with SDK retries 0/1.",
    )
    parser.add_argument("--repeat", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--deadline-seconds", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=20260821)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = asyncio.run(run(args))
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
