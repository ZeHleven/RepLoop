from __future__ import annotations

import json

import httpx

import scripts.observe_registry_read_enforcement as observer


def _shadow_report() -> dict[str, object]:
    check_types = (
        "route_allowlist",
        "constructed_tools",
        "argument_schema",
        "parallel_policy",
        "conditional_evidence",
        "observation_semantics",
    )
    return {
        "status": "match",
        "total_latency_ms": 2,
        "checks": [
            {
                "check_type": check_type,
                "status": "match",
                "mismatch_codes": [],
                "error_category": None,
            }
            for check_type in check_types
        ],
    }


def _completed_run() -> dict[str, object]:
    scenario = observer.SCENARIOS[1]
    action_tools = (
        "plan.get_active",
        "workout.get_progress",
        "profile.get_summary",
    )
    return {
        "id": "run-completed",
        "status": "completed",
        "duration_ms": 32100,
        "primary_intent": "plan_query",
        "execution_mode": scenario.expected_mode,
        "tool_allowlist": list(scenario.expected_allowlist),
        "execution_trace": {
            "trace_version": "1.1",
            "status": "completed",
            "terminal_action": "proposal",
            "termination_reason": "agent_completed",
            "budget_usage": {
                "plan_steps": 1,
                "model_calls": 2,
                "tool_calls": 3,
                "replans": 0,
            },
            "actions": [
                {"tool_id": tool_id, "status": "completed"}
                for tool_id in action_tools
            ],
            "observations": [
                {"tool_id": tool_id, "status": "success"}
                for tool_id in action_tools
            ],
            "stage_timings": [
                {
                    "stage": "planner",
                    "attempt": 1,
                    "source": "model",
                    "status": "success",
                    "latency_ms": 12000,
                    "error_category": None,
                    "input_chars": 800,
                    "output_chars": 300,
                    "input_tokens": 220,
                    "output_tokens": 90,
                    "finish_reason": "stop",
                    "raw_prompt": "must-not-be-persisted",
                },
                {
                    "stage": "tool_batch",
                    "attempt": 0,
                    "source": "controller",
                    "status": "success",
                    "latency_ms": 75,
                    "error_category": None,
                    "input_chars": None,
                    "output_chars": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "finish_reason": None,
                },
                {
                    "stage": "finalizer",
                    "attempt": 1,
                    "source": "model",
                    "status": "success",
                    "latency_ms": 9000,
                    "error_category": None,
                    "input_chars": 1600,
                    "output_chars": 500,
                    "input_tokens": 480,
                    "output_tokens": 150,
                    "finish_reason": "stop",
                },
            ],
            "tool_registry_shadow": _shadow_report(),
        },
        "reply": "ok",
        "cards": [{"type": tool_id} for tool_id in action_tools],
        "error_code": None,
    }


def test_completed_run_preserves_privacy_safe_stage_timing_diagnostics():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202,
                request=request,
                json={
                    "run_id": "run-completed",
                    "conversation_id": "conversation-1",
                },
            )
        return httpx.Response(200, request=request, json=_completed_run())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        item = observer._run_scenario(
            client,
            api_url="https://example.test/api/v1",
            headers={"Authorization": "Bearer secret"},
            scenario=observer.SCENARIOS[1],
            ordinal=1,
        )

    assert item["stage_latency_ms"] == {
        "planner": 12000,
        "tool_batch": 75,
        "finalizer": 9000,
    }
    assert item["stage_timings"][0] == {
        "stage": "planner",
        "attempt": 1,
        "source": "model",
        "status": "success",
        "latency_ms": 12000,
        "error_category": None,
        "input_chars": 800,
        "output_chars": 300,
        "input_tokens": 220,
        "output_tokens": 90,
        "finish_reason": "stop",
    }
    assert "raw_prompt" not in json.dumps(item)
    assert item["run_poll_count"] == 1
    assert item["business_failures"] == []


def test_http_failure_records_phase_status_and_safe_error_code_only():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            504,
            request=request,
            json={
                "code": "UPSTREAM_TIMEOUT",
                "message": "secret provider response must not be persisted",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        item = observer._run_scenario(
            client,
            api_url="https://example.test/api/v1",
            headers={"Authorization": "Bearer secret"},
            scenario=observer.SCENARIOS[2],
            ordinal=4,
        )

    assert item["status"] == "request_failed"
    assert item["request_phase"] == "agent_run_create"
    assert item["error_category"] == "unexpected_http_status"
    assert item["http_method"] == "POST"
    assert item["http_path"] == "/api/v1/agent/runs"
    assert item["http_status_code"] == 504
    assert item["response_error_code"] == "UPSTREAM_TIMEOUT"
    assert item["http_elapsed_ms"] >= 0
    assert item["business_failures"] == ["runner_request_failed"]
    serialized = json.dumps(item)
    assert "provider response" not in serialized
    assert "Bearer secret" not in serialized


def test_transport_timeout_records_phase_without_exception_message():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(
            "secret upstream hostname must not be persisted",
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        item = observer._run_scenario(
            client,
            api_url="https://example.test/api/v1",
            headers={"Authorization": "Bearer secret"},
            scenario=observer.SCENARIOS[2],
            ordinal=4,
        )

    assert item["status"] == "request_failed"
    assert item["request_phase"] == "agent_run_create"
    assert item["error_category"] == "request_timeout"
    assert item["http_status_code"] is None
    assert item["response_error_code"] is None
    assert "upstream hostname" not in json.dumps(item)


def test_durable_run_lifecycle_polls_until_terminal(monkeypatch):
    poll_statuses = [
        {
            "id": "run-completed",
            "status": "queued",
            "poll_after_ms": 800,
        },
        _completed_run(),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["client_request_id"].startswith(
                "registry-observe-"
            )
            return httpx.Response(
                202,
                request=request,
                json={
                    "run_id": "run-completed",
                    "conversation_id": "conversation-1",
                    "status": "queued",
                    "poll_after_ms": 800,
                },
            )
        return httpx.Response(
            200,
            request=request,
            json=poll_statuses.pop(0),
        )

    monkeypatch.setattr(observer.time, "sleep", lambda _seconds: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        item = observer._run_scenario(
            client,
            api_url="https://example.test/api/v1",
            headers={"Authorization": "Bearer secret"},
            scenario=observer.SCENARIOS[1],
            ordinal=1,
        )

    assert item["run_id"] == "run-completed"
    assert item["status"] == "completed"
    assert item["run_poll_count"] == 2
    assert item["business_failures"] == []
