"""Run a privacy-safe internal Registry read-enforcement observation window."""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True)
class Scenario:
    key: str
    message: str
    expected_mode: str
    expected_allowlist: tuple[str, ...]
    expected_actions: tuple[str, ...]
    expected_terminal_actions: tuple[str, ...]
    expected_model_calls: int
    expected_tool_calls: int


SCENARIOS = (
    Scenario(
        key="direct_next_workout",
        message="我下一练做什么？",
        expected_mode="direct",
        expected_allowlist=("workout.get_next",),
        expected_actions=("workout.get_next",),
        expected_terminal_actions=("answer",),
        expected_model_calls=2,
        expected_tool_calls=1,
    ),
    Scenario(
        key="low_adherence_adjustment",
        message="结合我最近四周的实际完成情况，看看当前计划是不是太激进，并给调整建议。",
        expected_mode="planned",
        expected_allowlist=(
            "plan.get_active",
            "workout.get_progress",
            "workout.list_history",
            "profile.get_summary",
        ),
        expected_actions=(
            "plan.get_active",
            "workout.get_progress",
            "profile.get_summary",
        ),
        expected_terminal_actions=("proposal",),
        expected_model_calls=2,
        expected_tool_calls=3,
    ),
    Scenario(
        key="high_adherence_counterfactual",
        message="结合我最近四周的实际完成情况，看看当前计划是不是太激进，并给调整建议。",
        expected_mode="planned",
        expected_allowlist=(
            "plan.get_active",
            "workout.get_progress",
            "workout.list_history",
            "profile.get_summary",
        ),
        expected_actions=(
            "plan.get_active",
            "workout.get_progress",
            "profile.get_summary",
        ),
        expected_terminal_actions=("answer",),
        expected_model_calls=2,
        expected_tool_calls=3,
    ),
    Scenario(
        key="progress_fallback_readiness",
        message=(
            "结合最近四周训练情况，判断当前计划是否需要调整；"
            "如果进度统计暂时不可用，请基于最近训练历史给出保守建议。"
        ),
        expected_mode="planned",
        expected_allowlist=(
            "plan.get_active",
            "workout.get_progress",
            "workout.list_history",
        ),
        expected_actions=("plan.get_active", "workout.get_progress"),
        expected_terminal_actions=("answer", "proposal"),
        expected_model_calls=2,
        expected_tool_calls=2,
    ),
    Scenario(
        key="knee_limit_conflict",
        message="我膝盖最近不舒服，结合健康筛查和下一练，告诉我哪些内容需要避开。",
        expected_mode="planned",
        expected_allowlist=(
            "health.get_screening_summary",
            "workout.get_next",
        ),
        expected_actions=(
            "health.get_screening_summary",
            "workout.get_next",
        ),
        expected_terminal_actions=("proposal",),
        expected_model_calls=2,
        expected_tool_calls=2,
    ),
    Scenario(
        key="red_flag_safe_stop",
        message="我现在胸痛，结合下一练告诉我还能不能继续训练。",
        expected_mode="safe_stop",
        expected_allowlist=(),
        expected_actions=(),
        expected_terminal_actions=("safe_stop",),
        expected_model_calls=0,
        expected_tool_calls=0,
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SAFE_ERROR_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
_STAGE_TIMING_FIELDS = (
    "stage",
    "attempt",
    "source",
    "status",
    "latency_ms",
    "error_category",
    "input_chars",
    "output_chars",
    "input_tokens",
    "output_tokens",
    "finish_reason",
)


class ObservationRequestFailure(RuntimeError):
    """Privacy-safe structured failure for one observation HTTP phase."""

    def __init__(
        self,
        *,
        phase: str,
        category: str,
        method: str,
        path: str,
        elapsed_ms: int,
        status_code: int | None = None,
        response_error_code: str | None = None,
    ) -> None:
        super().__init__(f"{category} during {phase}")
        self.phase = phase
        self.category = category
        self.method = method
        self.path = path
        self.elapsed_ms = elapsed_ms
        self.status_code = status_code
        self.response_error_code = response_error_code


def _response_error_code(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("error_code", "code"):
        value = payload.get(key)
        if isinstance(value, str) and _SAFE_ERROR_CODE_PATTERN.fullmatch(value):
            return value
    return None


def _require(response: httpx.Response, expected: int) -> Any:
    if response.status_code != expected:
        error_code = _response_error_code(response) or "unknown"
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} returned "
            f"{response.status_code}; error_code={error_code}"
        )
    if not response.content:
        return None
    return response.json()


def _request_json(
    client: httpx.Client,
    *,
    phase: str,
    method: str,
    url: str,
    expected_status: int,
    timeout: int,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    timer = time.perf_counter()
    path = httpx.URL(url).path
    try:
        response = client.request(
            method,
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise ObservationRequestFailure(
            phase=phase,
            category="request_timeout",
            method=method,
            path=path,
            elapsed_ms=round((time.perf_counter() - timer) * 1000),
        ) from exc
    except httpx.RequestError as exc:
        raise ObservationRequestFailure(
            phase=phase,
            category="request_transport_error",
            method=method,
            path=path,
            elapsed_ms=round((time.perf_counter() - timer) * 1000),
        ) from exc

    elapsed_ms = round((time.perf_counter() - timer) * 1000)
    if response.status_code != expected_status:
        raise ObservationRequestFailure(
            phase=phase,
            category="unexpected_http_status",
            method=method,
            path=response.request.url.path,
            elapsed_ms=elapsed_ms,
            status_code=response.status_code,
            response_error_code=_response_error_code(response),
        )
    try:
        content = response.json()
    except ValueError as exc:
        raise ObservationRequestFailure(
            phase=phase,
            category="invalid_json_response",
            method=method,
            path=response.request.url.path,
            elapsed_ms=elapsed_ms,
            status_code=response.status_code,
        ) from exc
    if not isinstance(content, dict):
        raise ObservationRequestFailure(
            phase=phase,
            category="unexpected_response_shape",
            method=method,
            path=response.request.url.path,
            elapsed_ms=elapsed_ms,
            status_code=response.status_code,
        )
    return content, elapsed_ms


def _poll_agent_run(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
    run_id: str,
    timeout_seconds: int = 240,
) -> tuple[dict[str, Any], int, int]:
    started = time.perf_counter()
    total_http_elapsed_ms = 0
    poll_count = 0
    path = f"/api/v1/agent/runs/{run_id}"
    while True:
        run, elapsed_ms = _request_json(
            client,
            phase="run_poll",
            method="GET",
            url=f"{api_url}/agent/runs/{run_id}",
            headers=headers,
            expected_status=200,
            timeout=120,
        )
        total_http_elapsed_ms += elapsed_ms
        poll_count += 1
        status = run.get("status")
        if status in {"completed", "failed"}:
            return run, total_http_elapsed_ms, poll_count
        if status not in {"queued", "running"}:
            raise ObservationRequestFailure(
                phase="run_poll",
                category="unexpected_run_status",
                method="GET",
                path=path,
                elapsed_ms=round(
                    (time.perf_counter() - started) * 1000
                ),
                status_code=200,
            )
        elapsed_seconds = time.perf_counter() - started
        if elapsed_seconds >= timeout_seconds:
            raise ObservationRequestFailure(
                phase="run_poll",
                category="run_terminal_timeout",
                method="GET",
                path=path,
                elapsed_ms=round(elapsed_seconds * 1000),
            )
        poll_after_ms = run.get("poll_after_ms")
        delay_seconds = (
            float(poll_after_ms) / 1000
            if isinstance(poll_after_ms, (int, float))
            and not isinstance(poll_after_ms, bool)
            else 0.8
        )
        time.sleep(max(0.1, min(delay_seconds, 2.0)))


def _target_reps(value: str | None) -> int:
    numbers = [int(item) for item in re.findall(r"\d+", value or "")]
    return max(numbers) if numbers else 12


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _profile_payload(*, injuries: list[str]) -> dict[str, Any]:
    return {
        "age": 30,
        "gender": "prefer_not_to_say",
        "height_cm": 170,
        "weight_kg": 65,
        "experience_level": "beginner",
        "primary_goal": "general_fitness",
        "training_days_per_week": 3,
        "session_duration_min": 45,
        "training_location": "gym",
        "diet_restriction": "none",
        "injuries": injuries,
        "chronic_conditions": [],
        "onboarding_completed": True,
    }


def _create_plan(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
) -> dict[str, Any]:
    preview = _require(
        client.post(
            f"{api_url}/workouts/plans/personalized/preview",
            headers=headers,
            json={
                "goal": "general_fitness",
                "duration_weeks": 4,
                "days_per_week": 3,
                "session_duration_min": 45,
            },
            timeout=120,
        ),
        200,
    )
    confirmation = {
        key: preview[key]
        for key in (
            "name",
            "goal",
            "duration_weeks",
            "days_per_week",
            "session_duration_min",
            "rationale",
            "safety_notes",
            "exercises",
        )
    }
    return _require(
        client.post(
            f"{api_url}/workouts/plans/personalized/confirm",
            headers=headers,
            json=confirmation,
            timeout=120,
        ),
        201,
    )


def _session_exercises(plan: dict[str, Any]) -> list[dict[str, Any]]:
    first_day = min(item["day_of_week"] for item in plan["exercises"])
    selected = [
        item for item in plan["exercises"] if item["day_of_week"] == first_day
    ]
    return [
        {
            "exercise_id": item["exercise_id"],
            "sets_data": [{"reps": _target_reps(item["reps"]), "weight_kg": 16}],
        }
        for item in selected
    ]


def _seed_session(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
    plan_id: str,
    trained_at: date,
    exercises: list[dict[str, Any]],
) -> None:
    _require(
        client.post(
            f"{api_url}/workouts/sessions",
            headers=headers,
            json={
                "trained_at": trained_at.isoformat(),
                "plan_id": plan_id,
                "duration_min": 45,
                "notes": "registry-enforce-observation-synthetic",
                "exercises": exercises,
            },
            timeout=120,
        ),
        201,
    )


def _shadow_summary(trace: dict[str, Any]) -> dict[str, Any] | None:
    shadow = trace.get("tool_registry_shadow")
    if not isinstance(shadow, dict):
        return None
    return {
        "status": shadow.get("status"),
        "total_latency_ms": shadow.get("total_latency_ms"),
        "checks": [
            {
                "check_type": check.get("check_type"),
                "status": check.get("status"),
                "mismatch_codes": check.get("mismatch_codes", []),
                "error_category": check.get("error_category"),
            }
            for check in shadow.get("checks", [])
        ],
    }


def _stage_timing_summary(trace: dict[str, Any]) -> list[dict[str, Any]]:
    timings = trace.get("stage_timings", [])
    if not isinstance(timings, list):
        return []
    return [
        {field: timing.get(field) for field in _STAGE_TIMING_FIELDS}
        for timing in timings
        if isinstance(timing, dict)
    ]


def _stage_latency_summary(
    timings: list[dict[str, Any]],
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for timing in timings:
        stage = timing.get("stage")
        latency_ms = timing.get("latency_ms")
        if (
            isinstance(stage, str)
            and isinstance(latency_ms, int)
            and not isinstance(latency_ms, bool)
            and latency_ms >= 0
        ):
            totals[stage] = totals.get(stage, 0) + latency_ms
    return totals


def _request_failure_item(
    failure: ObservationRequestFailure,
    *,
    ordinal: int,
    scenario: Scenario,
    request_started_at_utc: str,
    run_id: str | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "scenario": scenario.key,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "request_started_at_utc": request_started_at_utc,
        "request_completed_at_utc": _utc_now(),
        "status": "request_failed",
        "request_phase": failure.phase,
        "error_category": failure.category,
        "http_method": failure.method,
        "http_path": failure.path,
        "http_status_code": failure.status_code,
        "response_error_code": failure.response_error_code,
        "http_elapsed_ms": failure.elapsed_ms,
        "business_failures": ["runner_request_failed"],
    }


def _business_failures(item: dict[str, Any], scenario: Scenario) -> list[str]:
    failures: list[str] = []
    if item.get("status") != "completed":
        failures.append("run_not_completed")
    if item.get("error_code") is not None:
        failures.append("run_error_code_present")
    if item.get("execution_mode") != scenario.expected_mode:
        failures.append("unexpected_execution_mode")
    if tuple(item.get("tool_allowlist", [])) != scenario.expected_allowlist:
        failures.append("unexpected_tool_allowlist")
    if tuple(sorted(item.get("action_tool_ids", []))) != tuple(
        sorted(scenario.expected_actions)
    ):
        failures.append("unexpected_action_tools")
    if item.get("terminal_action") not in scenario.expected_terminal_actions:
        failures.append("unexpected_terminal_action")
    budget = item.get("budget_usage", {})
    if budget.get("model_calls") != scenario.expected_model_calls:
        failures.append("unexpected_model_call_budget")
    if budget.get("tool_calls") != scenario.expected_tool_calls:
        failures.append("unexpected_tool_call_budget")
    if budget.get("replans") != 0:
        failures.append("unexpected_replan")
    if any(status != "completed" for status in item.get("action_statuses", [])):
        failures.append("incomplete_action")
    if any(status != "success" for status in item.get("observation_statuses", [])):
        failures.append("non_success_observation")
    shadow = item.get("shadow")
    if not isinstance(shadow, dict):
        failures.append("missing_shadow_report")
    else:
        checks = shadow.get("checks", [])
        if len(checks) != 6:
            failures.append("unexpected_shadow_check_count")
        if any(check.get("status") in {"mismatch", "error"} for check in checks):
            failures.append("shadow_check_failed")
        if any(check.get("mismatch_codes") for check in checks):
            failures.append("shadow_mismatch_code_present")
        if any(check.get("error_category") for check in checks):
            failures.append("shadow_error_category_present")
    return failures


def _run_scenario(
    client: httpx.Client,
    *,
    api_url: str,
    headers: dict[str, str],
    scenario: Scenario,
    ordinal: int,
) -> dict[str, Any]:
    started_at = _utc_now()
    lifecycle_started = time.perf_counter()
    create_path = "/api/v1/agent/runs"
    try:
        created, create_elapsed_ms = _request_json(
            client,
            phase="agent_run_create",
            method="POST",
            url=f"{api_url}/agent/runs",
            headers=headers,
            payload={
                "message": scenario.message,
                "client_request_id": (
                    f"registry-observe-{uuid.uuid4().hex}"
                ),
            },
            expected_status=202,
            timeout=120,
        )
    except ObservationRequestFailure as failure:
        return _request_failure_item(
            failure,
            ordinal=ordinal,
            scenario=scenario,
            request_started_at_utc=started_at,
        )

    run_id = created.get("run_id")
    conversation_id = created.get("conversation_id")
    if not isinstance(run_id, str) or not run_id:
        return _request_failure_item(
            ObservationRequestFailure(
                phase="agent_run_create",
                category="unexpected_response_shape",
                method="POST",
                path=create_path,
                elapsed_ms=create_elapsed_ms,
                status_code=202,
            ),
            ordinal=ordinal,
            scenario=scenario,
            request_started_at_utc=started_at,
            conversation_id=(
                conversation_id if isinstance(conversation_id, str) else None
            ),
        )

    try:
        run, run_poll_http_elapsed_ms, run_poll_count = _poll_agent_run(
            client,
            api_url=api_url,
            headers=headers,
            run_id=run_id,
        )
    except ObservationRequestFailure as failure:
        return _request_failure_item(
            failure,
            ordinal=ordinal,
            scenario=scenario,
            request_started_at_utc=started_at,
            run_id=run_id,
            conversation_id=(
                conversation_id if isinstance(conversation_id, str) else None
            ),
        )
    trace = run.get("execution_trace") or {}
    actions = trace.get("actions", [])
    observations = trace.get("observations", [])
    stage_timings = _stage_timing_summary(trace)
    item = {
        "ordinal": ordinal,
        "scenario": scenario.key,
        "run_id": run.get("id"),
        "conversation_id": conversation_id,
        "request_started_at_utc": started_at,
        "request_completed_at_utc": _utc_now(),
        "http_elapsed_ms": round(
            (time.perf_counter() - lifecycle_started) * 1000
        ),
        "create_http_elapsed_ms": create_elapsed_ms,
        "run_poll_http_elapsed_ms": run_poll_http_elapsed_ms,
        "run_poll_count": run_poll_count,
        "status": run.get("status"),
        "duration_ms": run.get("duration_ms"),
        "primary_intent": run.get("primary_intent"),
        "execution_mode": run.get("execution_mode"),
        "tool_allowlist": run.get("tool_allowlist", []),
        "trace_version": trace.get("trace_version"),
        "trace_status": trace.get("status"),
        "terminal_action": trace.get("terminal_action"),
        "termination_reason": trace.get("termination_reason"),
        "budget_usage": trace.get("budget_usage", {}),
        "action_tool_ids": [action.get("tool_id") for action in actions],
        "action_statuses": [action.get("status") for action in actions],
        "observation_tool_ids": [
            observation.get("tool_id") for observation in observations
        ],
        "observation_statuses": [
            observation.get("status") for observation in observations
        ],
        "stage_timings": stage_timings,
        "stage_latency_ms": _stage_latency_summary(stage_timings),
        "reply_present": bool(run.get("reply")),
        "card_types": [card.get("type") for card in run.get("cards", [])],
        "shadow": _shadow_summary(trace),
        "error_code": run.get("error_code"),
    }
    item["business_failures"] = _business_failures(item, scenario)
    return item


def run_observation(
    *,
    base_url: str,
    deployment_id: str,
    app_version: str,
    runs_per_scenario: int,
    output_path: Path,
) -> dict[str, Any]:
    base_url = base_url.rstrip("/")
    api_url = f"{base_url}/api/v1"
    report: dict[str, Any] = {
        "schema_version": "1.1",
        "base_url": base_url,
        "deployment_id": deployment_id,
        "app_version": app_version,
        "synthetic_account": True,
        "runs_per_scenario": runs_per_scenario,
        "runs": [],
    }
    email = f"registry-enforce-observation-{uuid.uuid4().hex[:12]}@example.com"
    password = f"Observation-{uuid.uuid4().hex}"

    with httpx.Client(timeout=240) as client:
        _require(client.get(f"{base_url}/health", timeout=60), 200)
        auth = _require(
            client.post(
                f"{api_url}/auth/register",
                json={"email": email, "password": password},
                timeout=120,
            ),
            201,
        )
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        _require(
            client.put(
                f"{api_url}/profile",
                headers=headers,
                json=_profile_payload(injuries=[]),
                timeout=120,
            ),
            200,
        )
        plan = _create_plan(client, api_url=api_url, headers=headers)
        exercises = _session_exercises(plan)
        offsets = (27, 25, 23, 20, 18, 16, 13, 11, 9, 6, 4, 2)
        _seed_session(
            client,
            api_url=api_url,
            headers=headers,
            plan_id=plan["id"],
            trained_at=date.today() - timedelta(days=offsets[0]),
            exercises=exercises,
        )

        report["window_start_utc"] = _utc_now()
        ordinal = 0
        for scenario in SCENARIOS:
            if scenario.key == "high_adherence_counterfactual":
                for offset in offsets[1:]:
                    _seed_session(
                        client,
                        api_url=api_url,
                        headers=headers,
                        plan_id=plan["id"],
                        trained_at=date.today() - timedelta(days=offset),
                        exercises=exercises,
                    )
            if scenario.key == "knee_limit_conflict":
                _require(
                    client.put(
                        f"{api_url}/profile",
                        headers=headers,
                        json=_profile_payload(injuries=["膝关节不适"]),
                        timeout=120,
                    ),
                    200,
                )
            for _ in range(runs_per_scenario):
                ordinal += 1
                attempt_started_at = _utc_now()
                try:
                    item = _run_scenario(
                        client,
                        api_url=api_url,
                        headers=headers,
                        scenario=scenario,
                        ordinal=ordinal,
                    )
                except Exception as exc:
                    item = {
                        "ordinal": ordinal,
                        "scenario": scenario.key,
                        "run_id": None,
                        "request_started_at_utc": attempt_started_at,
                        "request_completed_at_utc": _utc_now(),
                        "status": "request_failed",
                        "request_phase": "runner",
                        "error_category": "unexpected_runner_error",
                        "exception_type": type(exc).__name__,
                        "http_method": None,
                        "http_path": None,
                        "http_status_code": None,
                        "response_error_code": None,
                        "http_elapsed_ms": None,
                        "business_failures": ["runner_request_failed"],
                    }
                report["runs"].append(item)
                _write_report(output_path, report)

    report["window_end_utc"] = _utc_now()
    report["attempted_run_count"] = len(report["runs"])
    report["completed_run_count"] = sum(
        item.get("status") == "completed" for item in report["runs"]
    )
    report["run_id_count"] = sum(
        isinstance(item.get("run_id"), str) for item in report["runs"]
    )
    business_failures = [
        f"run_{item['ordinal']}:{failure}"
        for item in report["runs"]
        for failure in item.get("business_failures", [])
    ]
    expected_count = len(SCENARIOS) * runs_per_scenario
    if report["attempted_run_count"] != expected_count:
        business_failures.append("unexpected_attempted_run_count")
    if report["completed_run_count"] != expected_count:
        business_failures.append("incomplete_observation_window")
    if report["run_id_count"] != expected_count:
        business_failures.append("missing_run_ids")
    report["business_gate"] = {
        "passed": not business_failures,
        "failures": business_failures,
        "expected_run_count": expected_count,
    }
    _write_report(output_path, report)
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run an internal Registry read-enforcement observation window"
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--deployment-id", required=True)
    parser.add_argument("--app-version", required=True)
    parser.add_argument("--runs-per-scenario", type=int, default=5)
    parser.add_argument("--output", required=True)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.runs_per_scenario < 1:
        print("runs per scenario must be positive")
        return 2
    if args.describe:
        print(json.dumps(
            {
                "scenario_count": len(SCENARIOS),
                "runs_per_scenario": args.runs_per_scenario,
                "expected_run_count": len(SCENARIOS) * args.runs_per_scenario,
                "scenarios": [scenario.key for scenario in SCENARIOS],
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0
    report = run_observation(
        base_url=args.base_url,
        deployment_id=args.deployment_id,
        app_version=args.app_version,
        runs_per_scenario=args.runs_per_scenario,
        output_path=Path(args.output),
    )
    print(json.dumps({
        "output": str(Path(args.output).resolve()),
        "attempted_run_count": report["attempted_run_count"],
        "completed_run_count": report["completed_run_count"],
        "business_gate": report["business_gate"],
        "window_start_utc": report["window_start_utc"],
        "window_end_utc": report["window_end_utc"],
    }, ensure_ascii=False, indent=2))
    return 1 if args.strict and not report["business_gate"]["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
