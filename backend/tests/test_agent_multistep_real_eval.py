from typing import Any

import pytest

from app.schemas.agent_planning import (
    ExecutorDecision,
    FinalResponse,
    MicroPlan,
    MicroPlanStep,
    PlannedToolAction,
)
from app.services.agent_controller import execute_planned_agent
from app.services.agent_intent import IntentResolution
from app.services.agent_runtime import _audit_result_summary
from app.services.agent_trace import build_initial_execution_trace
from evals.multistep_schema import load_multistep_dataset
from scripts.evaluate_agent_multistep_real import (
    _fast_path_gate_failures,
    _failure_summaries,
    _latency_summary,
    _percentile,
    _stage_latency_summary,
    _three_evidence_fast_path_summary,
    build_fixture_tools,
)


def _case(case_id: str):
    dataset = load_multistep_dataset()
    return next(item for item in dataset.cases if item.id == case_id)


class _FaultInjectionPolicy:
    def __init__(self):
        self.finalize_inputs: list[dict[str, Any]] = []

    async def create_plan(self, **kwargs: Any) -> MicroPlan:
        return MicroPlan(
            goal=kwargs["goal"],
            steps=[MicroPlanStep(
                objective="并行读取计划与聚合进度，失败时使用条件替代证据",
                candidate_tools=[
                    "workout.get_progress",
                    "plan.get_active",
                ],
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(
                        tool_id="workout.get_progress",
                        arguments={"weeks": 4},
                    ),
                    PlannedToolAction(
                        tool_id="plan.get_active",
                        arguments={},
                    ),
                ],
                success_signal="取得计划以及进度或历史替代证据",
            )],
        )

    async def decide_step(self, **_kwargs: Any) -> ExecutorDecision:
        raise AssertionError("fault recovery should not wake Executor")

    async def revise_plan(self, **_kwargs: Any) -> MicroPlan:
        raise AssertionError("fault recovery should not wake Replanner")

    async def finalize(self, **kwargs: Any) -> FinalResponse:
        self.finalize_inputs.append(kwargs)
        return FinalResponse(
            terminal_action="proposal",
            outcome="adjustment_proposal",
            reply="根据可用历史建议保守降频；该提案尚未保存，需你确认。",
        )


@pytest.mark.asyncio
async def test_retryable_tool_fixture_reaches_controller_as_timeout():
    case = _case("progress_timeout_falls_back_to_history")
    tools = build_fixture_tools(case, ["workout.get_progress"])

    with pytest.raises(TimeoutError, match="tool_timeout"):
        await tools[0].ainvoke({})


@pytest.mark.asyncio
async def test_injected_progress_timeout_recovers_through_controller():
    case = _case("progress_timeout_falls_back_to_history")
    allowlist = list(case.candidate_tools)
    resolution = IntentResolution(
        primary_intent="plan_query",
        resolved_query=case.message,
        expanded_intents=[
            "workout_progress_query",
            "workout_history_query",
        ],
        subtasks=["读取当前计划", "读取最近四周训练证据", "判断是否调整"],
        confidence=1.0,
    )
    policy = _FaultInjectionPolicy()

    result = await execute_planned_agent(
        db=None,
        user_id="fault-injection-user",
        run_id="fault-injection-progress-timeout",
        model=None,
        goal=case.message,
        subtasks=resolution.subtasks,
        tool_allowlist=allowlist,
        initial_trace=build_initial_execution_trace(resolution, allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=build_fixture_tools(case, allowlist),
        proposal_creation_enabled=True,
    )

    trace = result.execution_trace
    assert [item.tool_id for item in trace.actions] == [
        "plan.get_active",
        "workout.get_progress",
        "workout.list_history",
    ]
    assert [item.status for item in trace.observations] == [
        "success",
        "error",
        "success",
    ]
    assert trace.actions[0].batch_id == trace.actions[1].batch_id
    assert trace.actions[0].batch_id is not None
    assert trace.actions[0].batch_id.startswith("batch-")
    assert trace.actions[-1].batch_id is not None
    assert trace.actions[-1].batch_id.startswith("fallback-")
    assert trace.budget_usage.tool_calls == 3
    assert trace.budget_usage.model_calls == 2
    assert trace.budget_usage.replans == 0
    assert trace.terminal_action == "proposal"
    assert trace.termination_reason == "agent_completed"
    assert [item.stage for item in trace.stage_timings] == [
        "planner",
        "tool_batch",
        "tool_batch",
        "finalizer",
    ]
    assert policy.finalize_inputs[0]["allowed_outcomes"] == [
        "adjustment_proposal",
        "no_change_needed",
        "insufficient_evidence",
    ]
    finalizer_observations = policy.finalize_inputs[0]["observations"]
    progress = next(
        item
        for item in finalizer_observations
        if item["tool_id"] == "workout.get_progress"
    )
    assert progress["status"] == "error"
    assert progress["result"] == {
        "error": {"code": "TimeoutError", "retryable": True}
    }


def test_latency_summary_uses_interpolated_p50_and_p95():
    assert _percentile([10, 20, 30], 0.5) == 20
    assert _percentile([10, 20, 30], 0.95) == 29
    assert _latency_summary([10, 20, 30]) == {
        "count": 3,
        "mean_ms": 20,
        "p50_ms": 20,
        "p95_ms": 29,
        "min_ms": 10,
        "max_ms": 30,
    }


def test_stage_latency_summary_reports_invocation_and_per_run_totals():
    completed = [{
        "trace": {
            "stage_timings": [
                {
                    "stage": "executor",
                    "source": "model",
                    "status": "success",
                    "latency_ms": 100,
                },
                {
                    "stage": "executor",
                    "source": "model",
                    "status": "error",
                    "latency_ms": 200,
                },
            ],
        },
    }, {
        "trace": {
            "stage_timings": [{
                "stage": "executor",
                "source": "model",
                "status": "success",
                "latency_ms": 300,
            }],
        },
    }]

    summary = _stage_latency_summary(completed)["executor"]

    assert summary["success_count"] == 2
    assert summary["error_count"] == 1
    assert summary["source_counts"] == {"model": 3}
    assert summary["per_invocation"]["p50_ms"] == 200
    assert summary["per_run_total"]["p50_ms"] == 300


def test_failure_summary_keeps_scores_and_path_but_not_reply():
    summaries = _failure_summaries([{
        "id": "recovery",
        "sample": 2,
        "reply": "sensitive generated content",
        "score": {
            "deterministic_pass": False,
            "terminal_action_ok": False,
        },
        "trace": {
            "terminal_action": "answer",
            "termination_reason": "agent_completed",
            "actions": [{
                "tool_id": "workout.get_progress",
                "status": "failed",
                "arguments": {"private": "must-not-copy"},
            }],
            "plan": {
                "steps": [{
                    "id": "step_1",
                    "execution_strategy": "bounded_react",
                    "status": "completed",
                    "candidate_tools": ["workout.get_progress"],
                }],
            },
        },
    }])

    assert summaries[0]["tools"] == [{
        "tool_id": "workout.get_progress",
        "status": "failed",
    }]
    assert "reply" not in summaries[0]
    assert "arguments" not in summaries[0]["tools"][0]


def test_three_evidence_fast_path_metrics_and_gates_are_independent():
    case = _case("plan_fit_good_adherence")
    required_tools = [group[0] for group in case.expected.required_tool_groups]
    results = [{
        "id": case.id,
        "trace": {
            "plan": {"steps": [{
                "execution_strategy": "parallel_read",
                "planned_actions": [
                    {"tool_id": tool_id, "arguments": {}}
                    for tool_id in required_tools
                ],
            }]},
            "stage_timings": [
                {"stage": "planner"},
                {"stage": "tool_batch"},
                {"stage": "finalizer"},
            ],
        },
    }, {
        "id": case.id,
        "trace": {
            "plan": {"steps": [{
                "execution_strategy": "direct",
                "planned_actions": [],
            }]},
            "stage_timings": [{"stage": "executor"}],
        },
    }]

    summary = _three_evidence_fast_path_summary([case], results)

    assert summary["sample_count"] == 2
    assert summary["three_action_parallel_hit_rate"] == 0.5
    assert summary["zero_executor_rate"] == 0.5
    report = {"three_evidence_fast_path": summary}
    assert _fast_path_gate_failures(
        report,
        min_parallel_rate=0.5,
        min_zero_executor_rate=0.5,
    ) == []
    assert _fast_path_gate_failures(
        report,
        min_parallel_rate=0.8,
        min_zero_executor_rate=0.9,
    ) == [
        "three_action_parallel_hit_rate_below_threshold",
        "zero_executor_rate_below_threshold",
    ]
