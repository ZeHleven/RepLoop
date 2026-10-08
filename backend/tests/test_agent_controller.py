from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain.tools import tool

from app.config import Settings, settings
from app.schemas.agent_planning import (
    ExecutorDecision,
    FinalResponse,
    MicroPlan,
    MicroPlanStep,
    ModelInvocationMetrics,
    PlannedToolAction,
)
from app.services.agent_controller import (
    ToolAuditEvent,
    _finalization_outcomes_for_observations,
    _normalize_plan_fit_fast_path,
    _planner_deadline_fallback_plan,
    execute_planned_agent,
)
from app.services.agent_intent import IntentResolution
from app.services.agent_runtime import _audit_result_summary
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
)
from app.services.agent_planner import PlanningModelError
from app.services.agent_tools import (
    NoArguments,
    WorkoutHistoryArguments,
    WorkoutProgressArguments,
)
from app.services.agent_trace import build_initial_execution_trace


class ScriptedPolicy:
    def __init__(
        self,
        *,
        plan: MicroPlan,
        decisions: list[ExecutorDecision],
        revised_plan: MicroPlan | None = None,
        final_response: FinalResponse | None = None,
    ):
        self.plan = plan
        self.decisions = list(decisions)
        self.revised_plan = revised_plan
        self.final_response = final_response or FinalResponse(
            terminal_action="answer",
            reply="已根据真实工具结果完成回答。",
        )
        self.decision_inputs: list[dict[str, Any]] = []
        self.replan_inputs: list[dict[str, Any]] = []
        self.finalize_inputs: list[dict[str, Any]] = []

    async def create_plan(self, **_kwargs: Any) -> MicroPlan:
        return self.plan

    async def decide_step(self, **kwargs: Any) -> ExecutorDecision:
        self.decision_inputs.append(kwargs)
        if not self.decisions:
            raise AssertionError("scripted decisions exhausted")
        return self.decisions.pop(0)

    async def revise_plan(self, **kwargs: Any) -> MicroPlan:
        self.replan_inputs.append(kwargs)
        if self.revised_plan is None:
            raise AssertionError("unexpected replan")
        return self.revised_plan

    async def finalize(self, **kwargs: Any) -> FinalResponse:
        self.finalize_inputs.append(kwargs)
        return self.final_response


class FailingPlannerPolicy:
    async def create_plan(self, **_kwargs: Any) -> MicroPlan:
        raise PlanningModelError(
            "planner failed",
            stage="planner",
            category="literal_error@steps.0.execution_strategy",
        )


class SlowPlannerPolicy:
    async def create_plan(self, **_kwargs: Any) -> MicroPlan:
        await asyncio.sleep(1)
        raise AssertionError("planner deadline did not cancel the call")

    async def decide_step(self, **_kwargs: Any) -> ExecutorDecision:
        return _decision(
            "complete_step",
            step_summary="Planner 超时降级步骤已透明收口",
        )

    async def finalize(self, **_kwargs: Any) -> FinalResponse:
        return FinalResponse(
            terminal_action="answer",
            reply="Planner 超时，已使用受限降级计划。",
            outcome="insufficient_evidence",
        )


class SlowPlannerFailingFinalizerPolicy(SlowPlannerPolicy):
    async def finalize(self, **_kwargs: Any) -> FinalResponse:
        raise PlanningModelError(
            "finalizer failed after planner deadline",
            stage="finalizer",
            category="finalizer_provider_error",
        )


class SlowExecutorPolicy(ScriptedPolicy):
    async def decide_step(self, **kwargs: Any) -> ExecutorDecision:
        self.decision_inputs.append(kwargs)
        await asyncio.sleep(1)
        raise AssertionError("executor deadline did not cancel the call")


class SlowExecutorFailingFinalizerPolicy(SlowExecutorPolicy):
    async def finalize(self, **kwargs: Any) -> FinalResponse:
        self.finalize_inputs.append(kwargs)
        raise PlanningModelError(
            "finalizer failed after executor deadline",
            stage="finalizer",
            category="finalizer_provider_error",
        )


class SlowReplannerPolicy(ScriptedPolicy):
    async def revise_plan(self, **kwargs: Any) -> MicroPlan:
        self.replan_inputs.append(kwargs)
        await asyncio.sleep(1)
        raise AssertionError("replanner deadline did not cancel the call")


class SlowReplannerFailingFinalizerPolicy(SlowReplannerPolicy):
    async def finalize(self, **kwargs: Any) -> FinalResponse:
        self.finalize_inputs.append(kwargs)
        raise PlanningModelError(
            "finalizer failed after replanner deadline",
            stage="finalizer",
            category="finalizer_provider_error",
        )


def _planned_trace(tool_allowlist: list[str]):
    resolution = IntentResolution(
        primary_intent="active_workout_query",
        resolved_query="判断继续当前训练还是开始下一练",
        expanded_intents=["next_workout_query"],
        subtasks=["检查活动训练", "根据结果决定下一步"],
        confidence=0.9,
    )
    return build_initial_execution_trace(resolution, tool_allowlist)


def _two_step_plan(
    first_tools: list[str],
    second_tools: list[str],
    *,
    first_strategy: str = "direct",
) -> MicroPlan:
    return MicroPlan(
        goal="判断继续当前训练还是开始下一练",
        steps=[
            MicroPlanStep(
                objective="取得第一项必要证据",
                candidate_tools=first_tools,
                execution_strategy=first_strategy,
                success_signal="获得第一项工具观察",
            ),
            MicroPlanStep(
                objective="根据已有证据补充或完成判断",
                candidate_tools=second_tools,
                execution_strategy="direct",
                success_signal="已足以回答用户目标",
            ),
        ],
    )


def _decision(decision: str, **updates: Any) -> ExecutorDecision:
    payload: dict[str, Any] = {
        "decision": decision,
        "reason": updates.pop("reason", "测试决策"),
        **updates,
    }
    return ExecutorDecision.model_validate(payload)


def test_planning_deadline_defaults_are_role_specific():
    assert (
        Settings.model_fields["AGENT_PLANNER_TIMEOUT_SECONDS"].default
        == 15.0
    )
    assert (
        Settings.model_fields["AGENT_REPLANNER_TIMEOUT_SECONDS"].default
        == 30.0
    )


def test_clear_low_adherence_requires_adjustment_proposal():
    observations = [
        {
            "tool_id": "plan.get_active",
            "status": "success",
            "result": {
                "found": True,
                "plan": {"scheduled_days_per_week": 5},
            },
        },
        {
            "tool_id": "workout.get_progress",
            "status": "success",
            "result": {"weeks": 4, "total_sessions": 4},
        },
    ]

    assert _finalization_outcomes_for_observations(
        goal="看看当前计划是不是太激进，并给调整建议",
        subtasks=["判断计划适配度"],
        observations=observations,
        proposal_creation_enabled=True,
    ) == ["adjustment_proposal"]


def test_explicit_duration_command_requires_proposal_after_plan_read():
    observations = [{
        "tool_id": "plan.get_active",
        "status": "success",
        "result": {
            "found": True,
            "plan": {"duration_weeks": 6, "days_per_week": 4},
        },
    }]

    assert _finalization_outcomes_for_observations(
        goal=(
            "请把当前训练计划周期从6周延长到8周，"
            "其他内容保持不变，并生成待确认提案。"
        ),
        subtasks=["读取当前训练计划"],
        observations=observations,
        proposal_creation_enabled=True,
    ) == ["adjustment_proposal"]


def test_explicit_duration_command_does_not_force_proposal_when_flag_off():
    observations = [{
        "tool_id": "plan.get_active",
        "status": "success",
        "result": {"found": True, "plan": {"duration_weeks": 6}},
    }]

    assert _finalization_outcomes_for_observations(
        goal=(
            "请把当前训练计划周期从6周延长到8周，"
            "其他内容保持不变，并生成待确认提案。"
        ),
        subtasks=["读取当前训练计划"],
        observations=observations,
        proposal_creation_enabled=False,
    ) == [
        "informational_answer",
        "no_change_needed",
        "insufficient_evidence",
    ]


def test_high_adherence_with_matching_profile_requires_no_change_outcome():
    observations = [
        {
            "tool_id": "profile.get_summary",
            "status": "success",
            "result": {
                "found": True,
                "training_days_per_week": 4,
            },
        },
        {
            "tool_id": "plan.get_active",
            "status": "success",
            "result": {
                "found": True,
                "plan": {"days_per_week": 4},
            },
        },
        {
            "tool_id": "workout.get_progress",
            "status": "success",
            "result": {"weeks": 4, "total_sessions": 15},
        },
    ]

    assert _finalization_outcomes_for_observations(
        goal="判断当前计划是否需要调整",
        subtasks=["判断计划适配度"],
        observations=observations,
        proposal_creation_enabled=True,
    ) == ["no_change_needed"]


@pytest.mark.parametrize("profile_days", [None, 3])
def test_high_adherence_without_profile_alignment_keeps_dynamic_options(
    profile_days,
):
    observations = [
        {
            "tool_id": "plan.get_active",
            "status": "success",
            "result": {
                "found": True,
                "plan": {"days_per_week": 4},
            },
        },
        {
            "tool_id": "workout.get_progress",
            "status": "success",
            "result": {"weeks": 4, "total_sessions": 15},
        },
    ]
    if profile_days is not None:
        observations.insert(0, {
            "tool_id": "profile.get_summary",
            "status": "success",
            "result": {
                "found": True,
                "training_days_per_week": profile_days,
            },
        })

    assert _finalization_outcomes_for_observations(
        goal="判断当前计划是否需要调整",
        subtasks=["判断计划适配度"],
        observations=observations,
        proposal_creation_enabled=True,
    ) == [
        "adjustment_proposal",
        "no_change_needed",
        "insufficient_evidence",
    ]


def test_high_adherence_with_additional_signal_keeps_dynamic_options():
    observations = [
        {
            "tool_id": "profile.get_summary",
            "status": "success",
            "result": {
                "found": True,
                "training_days_per_week": 4,
            },
        },
        {
            "tool_id": "plan.get_active",
            "status": "success",
            "result": {
                "found": True,
                "plan": {"days_per_week": 4},
            },
        },
        {
            "tool_id": "workout.get_progress",
            "status": "success",
            "result": {"weeks": 4, "total_sessions": 15},
        },
        {
            "tool_id": "workout.list_history",
            "status": "success",
            "result": {"count": 1, "sessions": []},
        },
    ]

    assert _finalization_outcomes_for_observations(
        goal="判断当前计划是否需要调整",
        subtasks=["结合近期趋势判断计划适配度"],
        observations=observations,
        proposal_creation_enabled=True,
    ) == [
        "adjustment_proposal",
        "no_change_needed",
        "insufficient_evidence",
    ]


@pytest.mark.asyncio
async def test_shadow_session_does_not_change_planned_controller_behavior():
    @tool(
        "workout_get_progress",
        args_schema=WorkoutProgressArguments,
        description="读取训练进度",
    )
    async def progress(weeks: int = 8):
        return {"weeks": weeks, "total_sessions": 3}

    plan = MicroPlan(
        goal="判断是否需要查询训练进度",
        steps=[MicroPlanStep(
            objective="判断现有证据是否足够",
            candidate_tools=["workout.get_progress"],
            execution_strategy="direct",
            success_signal="完成判断",
        )],
    )

    async def run(shadow_session=None):
        policy = ScriptedPolicy(
            plan=plan,
            decisions=[_decision(
                "complete_step",
                step_summary="无需调用工具即可完成测试收口",
            )],
        )
        result = await execute_planned_agent(
            db=None,
            user_id="shadow-parity-user",
            run_id="shadow-parity-run",
            model=None,
            goal=plan.goal,
            subtasks=["验证旁路不改变控制流"],
            tool_allowlist=["workout.get_progress"],
            initial_trace=_planned_trace(["workout.get_progress"]),
            summarize_observation=_audit_result_summary,
            policy=policy,
            tools=[progress],
            shadow_session=shadow_session,
        )
        return result, policy

    baseline, baseline_policy = await run()
    session = ToolRegistryShadowSession(sample_bucket=9)
    shadowed, shadowed_policy = await run(session)

    assert shadowed.reply == baseline.reply
    assert shadowed.cards == baseline.cards
    assert shadowed.missing_slots == baseline.missing_slots
    assert shadowed.execution_trace.terminal_action == (
        baseline.execution_trace.terminal_action
    )
    assert shadowed.execution_trace.budget_usage == (
        baseline.execution_trace.budget_usage
    )
    assert shadowed.execution_trace.actions == baseline.execution_trace.actions
    assert shadowed.execution_trace.observations == (
        baseline.execution_trace.observations
    )
    assert len(shadowed_policy.decision_inputs) == len(
        baseline_policy.decision_inputs
    )
    assert {
        check_type: check.status
        for check_type, check in session.checks.items()
    } == {
        "constructed_tools": "match",
        "argument_schema": "match",
        "parallel_policy": "match",
    }


@pytest.mark.asyncio
async def test_parallel_read_success_runs_concurrently_with_zero_executor():
    all_started = asyncio.Event()
    started: list[str] = []
    history_calls: list[int] = []

    async def wait_for_batch(tool_id: str) -> dict[str, Any]:
        started.append(tool_id)
        if len(started) == 3:
            all_started.set()
        await asyncio.wait_for(all_started.wait(), timeout=0.5)
        return {
            "found": tool_id != "profile.get_summary",
            "source": tool_id,
        }

    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        return await wait_for_batch("profile.get_summary")

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        return await wait_for_batch("plan.get_active")

    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        return await wait_for_batch("workout.get_progress")

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        history_calls.append(limit)
        return {"count": 1, "sessions": []}

    parallel_tools = [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    allowlist = [*parallel_tools, "workout.list_history"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="结合资料、计划和进度判断适配度",
            steps=[
                MicroPlanStep(
                    objective="并行取得三项相互独立的必要证据",
                    candidate_tools=parallel_tools,
                    execution_strategy="parallel_read",
                    completion_policy="after_all_observations",
                    planned_actions=[
                        PlannedToolAction(tool_id=item, arguments={})
                        for item in parallel_tools
                    ],
                    success_signal="三项只读证据均已返回",
                ),
                MicroPlanStep(
                    objective="主进度失败时读取训练历史替代证据",
                    candidate_tools=["workout.list_history"],
                    execution_strategy="direct",
                    success_signal="取得历史替代证据",
                ),
            ],
        ),
        decisions=[],
    )
    audits: list[ToolAuditEvent] = []

    async def sink(_trace, audit):
        if audit is not None:
            audits.append(audit)

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-success",
        model=None,
        goal="结合资料、计划和进度判断适配度",
        subtasks=["读取资料", "读取计划", "读取进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        event_sink=sink,
        policy=policy,
        tools=[profile, plan, progress, history],
    )

    trace = result.execution_trace
    assert set(started) == set(parallel_tools)
    assert history_calls == []
    assert policy.decision_inputs == []
    assert [item.status for item in trace.plan.steps] == [
        "completed",
        "skipped",
    ]
    assert trace.plan.steps[0].execution_strategy == "parallel_read"
    assert trace.termination_reason == "agent_completed_evidence_covered"
    assert len(trace.actions) == len(trace.observations) == 3
    profile_observation = next(
        item
        for item in trace.observations
        if item.tool_id == "profile.get_summary"
    )
    assert profile_observation.status == "success"
    assert profile_observation.summary["found"] is False
    assert len({item.batch_id for item in trace.actions}) == 1
    assert {item.batch_id for item in trace.observations} == {
        trace.actions[0].batch_id
    }
    assert [item.status for item in audits] == [
        "completed",
        "completed",
        "completed",
    ]
    assert trace.budget_usage.model_calls == 2
    assert trace.budget_usage.tool_calls == 3
    assert [item.stage for item in trace.stage_timings] == [
        "planner",
        "tool_batch",
        "finalizer",
    ]
    assert policy.finalize_inputs[0]["steps"][1]["status"] == "skipped"
    assert "全部允许证据源" in (
        policy.finalize_inputs[0]["steps"][1]["summary"]
    )


@pytest.mark.asyncio
async def test_parallel_read_primary_error_invokes_conditional_fallback():
    calls: list[str] = []

    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        calls.append("profile.get_summary")
        return {"found": True}

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        calls.append("plan.get_active")
        return {"found": True, "plan": {"name": "当前计划"}}

    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        calls.append("workout.get_progress")
        raise TimeoutError("fixture timeout")

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        calls.append("workout.list_history")
        return {"count": 1, "sessions": [{"id": "session-1"}]}

    primary_tools = [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    allowlist = [*primary_tools, "workout.list_history"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="结合资料、计划和训练证据判断适配度",
            steps=[
                MicroPlanStep(
                    objective="并行取得三项主证据",
                    candidate_tools=primary_tools,
                    execution_strategy="parallel_read",
                    completion_policy="after_all_observations",
                    planned_actions=[
                        PlannedToolAction(tool_id=item, arguments={})
                        for item in primary_tools
                    ],
                    success_signal="三项主证据均已解析",
                ),
                MicroPlanStep(
                    objective="进度失败时补充训练历史",
                    candidate_tools=["workout.list_history"],
                    execution_strategy="direct",
                    success_signal="取得训练历史替代证据",
                ),
            ],
        ),
        decisions=[],
    )
    audits: list[ToolAuditEvent] = []

    async def sink(_trace, audit):
        if audit is not None:
            audits.append(audit)

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-conditional-fallback",
        model=None,
        goal="结合资料、计划和训练证据判断适配度",
        subtasks=["读取资料", "读取计划", "读取训练证据"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        event_sink=sink,
        policy=policy,
        tools=[profile, plan, progress, history],
    )

    trace = result.execution_trace
    assert set(calls) == set(allowlist)
    assert policy.decision_inputs == []
    assert [item.status for item in trace.plan.steps] == [
        "completed",
        "skipped",
    ]
    assert trace.termination_reason == "agent_completed_evidence_covered"
    assert [item.tool_id for item in trace.actions] == allowlist
    assert [item.status for item in trace.observations] == [
        "success",
        "success",
        "error",
        "success",
    ]
    assert trace.actions[-1].batch_id is not None
    assert trace.actions[-1].batch_id.startswith("fallback-")
    assert [item.status for item in audits] == [
        "completed",
        "completed",
        "failed",
        "completed",
    ]
    assert trace.budget_usage.tool_calls == 4
    assert trace.budget_usage.model_calls == 2
    assert [item.error_category for item in trace.stage_timings] == [
        None,
        "parallel_tool_failure",
        None,
        None,
    ]


@pytest.mark.asyncio
async def test_parallel_read_conditional_fallback_failure_wakes_executor():
    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        return {"found": True}

    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        raise TimeoutError("progress timeout")

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        raise TimeoutError(f"history timeout at limit={limit}")

    primary_tools = ["profile.get_summary", "workout.get_progress"]
    allowlist = [*primary_tools, "workout.list_history"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="读取近期训练证据",
            steps=[MicroPlanStep(
                objective="并行读取资料与聚合训练进度",
                candidate_tools=primary_tools,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(tool_id=item, arguments={})
                    for item in primary_tools
                ],
                success_signal="取得进度或历史替代证据",
            )],
        ),
        decisions=[_decision(
            "complete_step",
            step_summary="进度与历史均读取失败，透明保守收口",
        )],
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-conditional-fallback-failure",
        model=None,
        goal="读取近期训练证据",
        subtasks=["读取进度", "失败时读取历史"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, progress, history],
    )

    trace = result.execution_trace
    assert len(policy.decision_inputs) == 1
    assert policy.decision_inputs[0]["remaining_step_tool_calls"] == 0
    assert [item.tool_id for item in trace.actions] == allowlist
    assert [item.status for item in trace.observations] == [
        "success",
        "error",
        "error",
    ]
    assert [item.error_category for item in trace.stage_timings] == [
        None,
        "parallel_tool_failure",
        "conditional_fallback_failure",
        None,
        None,
    ]


@pytest.mark.asyncio
async def test_parallel_read_not_found_invokes_next_workout_fallback():
    next_calls = 0

    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        return {"found": True}

    @tool(
        "workout_get_active_session",
        args_schema=NoArguments,
        description="读取活动训练",
    )
    async def active_session():
        return {"found": False}

    @tool(
        "workout_get_next",
        args_schema=NoArguments,
        description="读取下一练",
    )
    async def next_workout():
        nonlocal next_calls
        next_calls += 1
        return {"found": True, "day_of_week": 1}

    primary_tools = [
        "profile.get_summary",
        "workout.get_active_session",
    ]
    allowlist = [*primary_tools, "workout.get_next"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="判断继续训练还是开始下一练",
            steps=[
                MicroPlanStep(
                    objective="读取资料与活动训练主证据",
                    candidate_tools=primary_tools,
                    execution_strategy="parallel_read",
                    completion_policy="after_all_observations",
                    planned_actions=[
                        PlannedToolAction(tool_id=item, arguments={})
                        for item in primary_tools
                    ],
                    success_signal="活动训练状态已解析",
                ),
                MicroPlanStep(
                    objective="没有活动训练时读取下一练",
                    candidate_tools=["workout.get_next"],
                    execution_strategy="direct",
                    success_signal="取得下一练",
                ),
            ],
        ),
        decisions=[],
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-not-found-fallback",
        model=None,
        goal="判断继续训练还是开始下一练",
        subtasks=["读取活动训练", "必要时读取下一练"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, active_session, next_workout],
    )

    trace = result.execution_trace
    assert next_calls == 1
    assert policy.decision_inputs == []
    assert [item.tool_id for item in trace.actions] == allowlist
    assert [item.status for item in trace.plan.steps] == [
        "completed",
        "skipped",
    ]
    assert trace.termination_reason == "agent_completed_evidence_covered"
    assert trace.budget_usage.tool_calls == 3
    assert trace.budget_usage.model_calls == 2


@pytest.mark.asyncio
async def test_active_session_error_does_not_trigger_next_workout_fallback():
    next_calls = 0

    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        return {"found": True}

    @tool(
        "workout_get_active_session",
        args_schema=NoArguments,
        description="读取活动训练",
    )
    async def active_session():
        raise TimeoutError("active session timeout")

    @tool(
        "workout_get_next",
        args_schema=NoArguments,
        description="读取下一练",
    )
    async def next_workout():
        nonlocal next_calls
        next_calls += 1
        return {"found": True}

    primary_tools = [
        "profile.get_summary",
        "workout.get_active_session",
    ]
    allowlist = [*primary_tools, "workout.get_next"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="判断继续训练还是开始下一练",
            steps=[MicroPlanStep(
                objective="读取资料与活动训练主证据",
                candidate_tools=primary_tools,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(tool_id=item, arguments={})
                    for item in primary_tools
                ],
                success_signal="活动训练状态已解析",
            )],
        ),
        decisions=[_decision(
            "complete_step",
            step_summary="活动训练读取失败，未误判为没有活动训练",
        )],
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-active-session-error",
        model=None,
        goal="判断继续训练还是开始下一练",
        subtasks=["读取活动训练", "必要时读取下一练"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, active_session, next_workout],
    )

    trace = result.execution_trace
    assert next_calls == 0
    assert len(policy.decision_inputs) == 1
    assert [item.tool_id for item in trace.actions] == primary_tools
    assert [item.status for item in trace.observations] == [
        "success",
        "error",
    ]
    assert "conditional_fallback_failure" not in {
        item.error_category for item in trace.stage_timings
    }


@pytest.mark.asyncio
async def test_parallel_read_does_not_stop_before_unread_allowlisted_tool():
    calls: list[str] = []

    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        calls.append("profile.get_summary")
        return {"found": True}

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        calls.append("plan.get_active")
        return {"found": True, "plan": {"name": "当前计划"}}

    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        calls.append("workout.get_progress")
        return {"weeks": 4, "total_sessions": 3}

    parallel_tools = ["profile.get_summary", "plan.get_active"]
    allowlist = [*parallel_tools, "workout.get_progress"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="结合资料、计划和进度判断适配度",
            steps=[
                MicroPlanStep(
                    objective="先并行取得资料和计划",
                    candidate_tools=parallel_tools,
                    execution_strategy="parallel_read",
                    completion_policy="after_all_observations",
                    planned_actions=[
                        PlannedToolAction(tool_id=item, arguments={})
                        for item in parallel_tools
                    ],
                    success_signal="资料和计划均已返回",
                ),
                MicroPlanStep(
                    objective="补充尚未查询的训练进度",
                    candidate_tools=["workout.get_progress"],
                    execution_strategy="direct",
                    completion_policy="after_successful_observation",
                    success_signal="训练进度已返回",
                ),
            ],
        ),
        decisions=[_decision(
            "call_tool",
            tool_id="workout.get_progress",
            arguments={},
        )],
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-incomplete-coverage",
        model=None,
        goal="结合资料、计划和进度判断适配度",
        subtasks=["读取资料", "读取计划", "读取进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, plan, progress],
    )

    trace = result.execution_trace
    assert set(calls) == set(allowlist)
    assert len(policy.decision_inputs) == 1
    assert [item.status for item in trace.plan.steps] == [
        "completed",
        "completed",
    ]
    assert trace.termination_reason == "agent_completed"
    assert len(trace.actions) == len(trace.observations) == 3
    assert trace.budget_usage.model_calls == 3


@pytest.mark.asyncio
async def test_parallel_read_failure_wakes_executor_once_with_all_observations():
    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        raise TimeoutError("fixture timeout")

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        return {"found": True, "plan": {"name": "当前计划"}}

    allowlist = ["profile.get_summary", "plan.get_active"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="取得资料和计划",
            steps=[MicroPlanStep(
                objective="并行取得资料与计划",
                candidate_tools=allowlist,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(tool_id=item, arguments={})
                    for item in allowlist
                ],
                success_signal="资料和计划均已返回",
            )],
        ),
        decisions=[_decision(
            "complete_step",
            step_summary="计划已返回，资料读取失败已透明记录",
        )],
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-partial",
        model=None,
        goal="取得资料和计划",
        subtasks=["读取资料", "读取计划"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, plan],
    )

    trace = result.execution_trace
    assert len(policy.decision_inputs) == 1
    assert policy.decision_inputs[0]["remaining_step_tool_calls"] == 0
    assert [item.status for item in trace.observations] == [
        "error",
        "success",
    ]
    assert trace.plan.steps[0].status == "completed"
    batch_timing = next(
        item for item in trace.stage_timings if item.stage == "tool_batch"
    )
    assert batch_timing.status == "error"
    assert batch_timing.error_category == "parallel_tool_failure"
    assert trace.budget_usage.model_calls == 3


@pytest.mark.asyncio
async def test_parallel_read_rejects_observation_dependent_tool_pair():
    @tool(
        "workout_get_active_session",
        args_schema=NoArguments,
        description="读取活动训练",
    )
    async def active_session():
        return {"found": False}

    @tool(
        "workout_get_next",
        args_schema=NoArguments,
        description="读取下一练",
    )
    async def next_workout():
        return {"found": True}

    allowlist = ["workout.get_active_session", "workout.get_next"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="继续训练或开始下一练",
            steps=[MicroPlanStep(
                objective="错误地预取条件分支的两个工具",
                candidate_tools=allowlist,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(tool_id=item, arguments={})
                    for item in allowlist
                ],
                success_signal="已取得分支证据",
            )],
        ),
        decisions=[],
    )
    shadow_session = ToolRegistryShadowSession(sample_bucket=13)

    with pytest.raises(
        ValueError,
        match="observation-dependent alternatives",
    ):
        await execute_planned_agent(
            db=None,
            user_id="user-1",
            run_id="parallel-conditional-pair",
            model=None,
            goal="继续训练或开始下一练",
            subtasks=["查询活动训练", "必要时查询下一练"],
            tool_allowlist=allowlist,
            initial_trace=_planned_trace(allowlist),
            summarize_observation=_audit_result_summary,
            policy=policy,
            tools=[active_session, next_workout],
            shadow_session=shadow_session,
        )

    parallel_check = shadow_session.checks["parallel_policy"]
    assert parallel_check.status == "mismatch"
    assert parallel_check.mismatch_codes == ("parallel_policy_mismatch",)


@pytest.mark.asyncio
async def test_parallel_read_failure_can_request_one_replan():
    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户资料",
    )
    async def profile():
        raise TimeoutError("fixture timeout")

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        return {"found": True}

    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        return {"weeks": 4, "total_sessions": 3}

    parallel_tools = ["profile.get_summary", "plan.get_active"]
    allowlist = [*parallel_tools, "workout.get_progress"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="结合资料、计划和进度判断",
            steps=[MicroPlanStep(
                objective="并行取得资料和计划",
                candidate_tools=parallel_tools,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(tool_id=item, arguments={})
                    for item in parallel_tools
                ],
                success_signal="资料和计划均已返回",
            )],
        ),
        decisions=[
            _decision(
                "request_replan",
                reason="资料失败，改用训练进度形成保守结论",
            ),
            _decision(
                "call_tool",
                tool_id="workout.get_progress",
                arguments={},
            ),
        ],
        revised_plan=MicroPlan(
            goal="结合现有计划和训练进度判断",
            steps=[MicroPlanStep(
                objective="补充训练进度",
                candidate_tools=["workout.get_progress"],
                execution_strategy="direct",
                completion_policy="after_successful_observation",
                success_signal="训练进度已返回",
            )],
        ),
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="parallel-replan",
        model=None,
        goal="结合资料、计划和进度判断",
        subtasks=["读取必要证据", "形成判断"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, plan, progress],
    )

    trace = result.execution_trace
    assert len(policy.replan_inputs) == 1
    assert len(policy.decision_inputs) == 2
    assert policy.decision_inputs[0]["remaining_step_tool_calls"] == 0
    assert trace.budget_usage.replans == 1
    assert [item.plan_version for item in trace.actions] == [1, 1, 2]
    assert [item.tool_id for item in trace.actions] == [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    assert trace.plan.steps[0].status == "completed"


def test_planner_deadline_fallback_parallelizes_independent_primary_reads():
    plan = _planner_deadline_fallback_plan(
        goal="判断计划是否需要调整",
        tool_catalog=[
            {"tool_id": "plan.get_active", "description": "读取计划"},
            {
                "tool_id": "workout.get_progress",
                "description": "读取聚合进度",
            },
            {
                "tool_id": "workout.list_history",
                "description": "读取历史",
            },
        ],
        max_steps=3,
    )

    assert len(plan.steps) == 1
    assert plan.steps[0].candidate_tools == [
        "plan.get_active",
        "workout.get_progress",
    ]
    assert plan.steps[0].execution_strategy == "parallel_read"
    assert [
        item.tool_id for item in plan.steps[0].planned_actions
    ] == ["plan.get_active", "workout.get_progress"]


def test_planner_deadline_fallback_builds_three_action_fast_path():
    plan = _planner_deadline_fallback_plan(
        goal="结合偏好、计划和四周进度判断适配度",
        tool_catalog=[
            {"tool_id": "plan.get_active", "description": "读取计划"},
            {
                "tool_id": "workout.get_progress",
                "description": "读取聚合进度",
            },
            {
                "tool_id": "workout.list_history",
                "description": "读取历史备用",
            },
            {
                "tool_id": "profile.get_summary",
                "description": "读取偏好",
            },
        ],
        max_steps=3,
    )

    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.execution_strategy == "parallel_read"
    assert step.completion_policy == "after_all_observations"
    assert step.candidate_tools == [
        "plan.get_active",
        "workout.get_progress",
        "profile.get_summary",
    ]
    progress_action = next(
        item
        for item in step.planned_actions
        if item.tool_id == "workout.get_progress"
    )
    assert progress_action.arguments == {"weeks": 4}


def _plan_fit_tool_catalog():
    return [
        {"tool_id": "profile.get_summary", "description": "读取偏好"},
        {"tool_id": "plan.get_active", "description": "读取计划"},
        {
            "tool_id": "workout.get_progress",
            "description": "读取聚合进度",
        },
        {
            "tool_id": "workout.list_history",
            "description": "读取训练历史",
        },
    ]


def test_plan_fit_fast_path_preserves_explicit_history_analysis():
    plan = MicroPlan(
        goal="分析训练下降趋势",
        steps=[
            MicroPlanStep(
                objective="读取三项当前证据",
                candidate_tools=[
                    "profile.get_summary",
                    "plan.get_active",
                    "workout.get_progress",
                ],
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(
                        tool_id="profile.get_summary",
                        arguments={},
                    ),
                    PlannedToolAction(
                        tool_id="plan.get_active",
                        arguments={},
                    ),
                    PlannedToolAction(
                        tool_id="workout.get_progress",
                        arguments={"weeks": 4},
                    ),
                ],
                success_signal="当前证据均已返回",
            ),
            MicroPlanStep(
                objective="读取训练历史确认趋势",
                candidate_tools=["workout.list_history"],
                execution_strategy="direct",
                success_signal="历史趋势已返回",
            ),
        ],
    )

    normalized, changed = _normalize_plan_fit_fast_path(
        plan,
        goal="最近训练越来越少，结合历史分析下降趋势",
        subtasks=["确认长期变化"],
        tool_catalog=_plan_fit_tool_catalog(),
    )

    assert changed is False
    assert normalized is plan


def test_plan_fit_fast_path_restores_missing_primary_evidence():
    plan = MicroPlan(
        goal="判断计划适配度",
        steps=[MicroPlanStep(
            objective="读取当前计划",
            candidate_tools=["plan.get_active"],
            execution_strategy="direct",
            completion_policy="after_successful_observation",
            success_signal="当前计划已返回",
        )],
    )

    normalized, changed = _normalize_plan_fit_fast_path(
        plan,
        goal=(
            "结合我最近四周的实际完成情况，看看当前计划是不是太激进，"
            "并给调整建议。"
        ),
        subtasks=["判断计划适配度"],
        tool_catalog=_plan_fit_tool_catalog(),
    )

    assert changed is True
    assert len(normalized.steps) == 1
    step = normalized.steps[0]
    assert step.execution_strategy == "parallel_read"
    assert step.completion_policy == "after_all_observations"
    assert step.candidate_tools == [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    assert [item.tool_id for item in step.planned_actions] == [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    assert step.planned_actions[2].arguments == {"weeks": 4}


def test_plan_progress_fast_path_normalizes_conditional_history_route():
    plan = MicroPlan(
        goal="进度失败时使用历史作为替代证据",
        steps=[
            MicroPlanStep(
                objective="读取活动计划",
                candidate_tools=["plan.get_active"],
                execution_strategy="direct",
                completion_policy="after_successful_observation",
                success_signal="活动计划已返回",
            ),
            MicroPlanStep(
                objective="读取进度，失败时查询历史",
                candidate_tools=[
                    "workout.get_progress",
                    "workout.list_history",
                ],
                execution_strategy="bounded_react",
                completion_policy="executor_decides",
                success_signal="取得进度或历史替代证据",
            ),
        ],
    )
    tool_catalog = [
        item
        for item in _plan_fit_tool_catalog()
        if item["tool_id"] != "profile.get_summary"
    ]

    normalized, changed = _normalize_plan_fit_fast_path(
        plan,
        goal=(
            "结合最近四周训练情况判断当前计划是否需要调整；"
            "如果进度统计不可用，"
            "请基于最近训练历史给出保守建议"
        ),
        subtasks=["读取计划和四周进度"],
        tool_catalog=tool_catalog,
    )

    assert changed is True
    assert len(normalized.steps) == 1
    step = normalized.steps[0]
    assert step.execution_strategy == "parallel_read"
    assert step.candidate_tools == [
        "plan.get_active",
        "workout.get_progress",
    ]
    assert [item.tool_id for item in step.planned_actions] == [
        "plan.get_active",
        "workout.get_progress",
    ]
    assert step.planned_actions[1].arguments == {"weeks": 4}


def test_plan_fit_fast_path_does_not_remark_canonical_plan():
    plan = MicroPlan(
        goal="判断计划适配度",
        steps=[MicroPlanStep(
            objective="并行读取三项主证据",
            candidate_tools=[
                "profile.get_summary",
                "plan.get_active",
                "workout.get_progress",
            ],
            execution_strategy="parallel_read",
            completion_policy="after_all_observations",
            planned_actions=[
                PlannedToolAction(
                    tool_id="profile.get_summary",
                    arguments={},
                ),
                PlannedToolAction(
                    tool_id="plan.get_active",
                    arguments={},
                ),
                PlannedToolAction(
                    tool_id="workout.get_progress",
                    arguments={"weeks": 4},
                ),
            ],
            success_signal="三项主证据均已返回",
        )],
    )

    normalized, changed = _normalize_plan_fit_fast_path(
        plan,
        goal="结合最近四周完成情况判断计划适配度",
        subtasks=["判断计划适配度"],
        tool_catalog=_plan_fit_tool_catalog(),
    )

    assert changed is False
    assert normalized is plan


@pytest.mark.asyncio
async def test_planner_error_timing_is_emitted_before_failure():
    traces = []

    async def sink(trace, _audit):
        traces.append(trace)

    allowlist = ["workout.get_progress"]
    with pytest.raises(PlanningModelError):
        await execute_planned_agent(
            db=None,
            user_id="user-1",
            run_id="planner-error",
            model=None,
            goal="读取最近训练进度",
            subtasks=["读取进度"],
            tool_allowlist=allowlist,
            initial_trace=_planned_trace(allowlist),
            summarize_observation=_audit_result_summary,
            event_sink=sink,
            policy=FailingPlannerPolicy(),
            tools=[],
        )

    timing = traces[-1].stage_timings[-1]
    assert timing.stage == "planner"
    assert timing.status == "error"
    assert timing.error_category == (
        "literal_error@steps.0.execution_strategy"
    )
    assert traces[-1].budget_usage.model_calls == 0


@pytest.mark.asyncio
async def test_planner_has_independent_hard_deadline(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_TIMEOUT_SECONDS", 0.01)
    traces = []

    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        return {"weeks": 4}

    async def sink(trace, _audit):
        traces.append(trace)

    allowlist = ["workout.get_progress"]
    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="planner-deadline",
        model=None,
        goal="读取最近训练进度",
        subtasks=["读取进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        event_sink=sink,
        policy=SlowPlannerPolicy(),
        tools=[progress],
    )

    trace = result.execution_trace
    timing = next(
        item for item in trace.stage_timings if item.stage == "planner"
    )
    assert timing.stage == "planner"
    assert timing.status == "error"
    assert timing.error_category == "planner_deadline_exceeded"
    assert trace.plan.planner_source == "deadline_fallback_v1"
    assert trace.plan.revision_reason == "planner_deadline_exceeded"
    assert "planner_deadline_fallback" in trace.mode_reasons


@pytest.mark.asyncio
async def test_planner_deadline_finalizer_failure_uses_safe_reply(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_PLANNER_TIMEOUT_SECONDS", 0.01)

    @tool(
        "workout_get_progress",
        args_schema=WorkoutProgressArguments,
        description="读取训练进度",
    )
    async def progress(weeks: int = 8):
        return {"weeks": weeks, "total_sessions": 2}

    allowlist = ["workout.get_progress"]
    result = await execute_planned_agent(
        db=None,
        user_id="user-planner-finalizer-deadline",
        run_id="planner-finalizer-deadline",
        model=None,
        goal="读取最近四周训练进度",
        subtasks=["读取训练进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=SlowPlannerFailingFinalizerPolicy(),
        tools=[progress],
    )

    trace = result.execution_trace
    assert trace.status == "completed"
    assert trace.terminal_action == "answer"
    assert trace.termination_reason == "planner_deadline_exceeded"
    assert trace.finalization_contract is not None
    assert trace.finalization_contract.allowed_outcomes == [
        "insufficient_evidence"
    ]
    assert "deadline_finalizer_safe_fallback" in trace.mode_reasons
    finalizer_timing = next(
        item for item in trace.stage_timings
        if item.stage == "finalizer"
    )
    assert finalizer_timing.status == "error"
    assert finalizer_timing.error_category == "finalizer_provider_error"
    assert "没有修改" in result.reply


@pytest.mark.asyncio
async def test_executor_deadline_finishes_with_partial_evidence(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXECUTOR_TIMEOUT_SECONDS", 0.01)
    allowlist = ["workout.get_progress"]
    policy = SlowExecutorPolicy(
        plan=MicroPlan(
            goal="读取训练进度",
            steps=[MicroPlanStep(
                objective="读取训练进度",
                candidate_tools=allowlist,
                execution_strategy="direct",
                success_signal="获得进度观察",
            )],
        ),
        decisions=[],
        final_response=FinalResponse(
            terminal_action="answer",
            reply="执行决策超时，本轮没有足够证据。",
            outcome="insufficient_evidence",
        ),
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="executor-deadline",
        model=None,
        goal="读取最近训练进度",
        subtasks=["读取进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[],
    )

    trace = result.execution_trace
    assert trace.termination_reason == "executor_deadline_exceeded"
    assert trace.plan.steps[0].status == "failed"
    executor_timing = next(
        item for item in trace.stage_timings if item.stage == "executor"
    )
    assert executor_timing.status == "error"
    assert executor_timing.error_category == "executor_deadline_exceeded"
    assert trace.budget_usage.model_calls == 2


@pytest.mark.asyncio
async def test_executor_deadline_finalizer_failure_uses_safe_reply(monkeypatch):
    monkeypatch.setattr(settings, "AGENT_EXECUTOR_TIMEOUT_SECONDS", 0.01)
    allowlist = ["workout.get_progress"]
    policy = SlowExecutorFailingFinalizerPolicy(
        plan=MicroPlan(
            goal="读取训练进度",
            steps=[MicroPlanStep(
                objective="读取训练进度",
                candidate_tools=allowlist,
                execution_strategy="direct",
                success_signal="获得进度观察",
            )],
        ),
        decisions=[],
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-executor-finalizer-deadline",
        run_id="executor-finalizer-deadline",
        model=None,
        goal="读取最近训练进度",
        subtasks=["读取进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[],
    )

    trace = result.execution_trace
    assert trace.status == "completed"
    assert trace.termination_reason == "executor_deadline_exceeded"
    assert trace.terminal_action == "answer"
    assert trace.finalization_contract is not None
    assert trace.finalization_contract.allowed_outcomes == [
        "insufficient_evidence"
    ]
    assert "deadline_finalizer_safe_fallback" in trace.mode_reasons
    assert "没有修改" in result.reply


@pytest.mark.asyncio
async def test_finalizer_contract_maps_adjustment_outcome_to_proposal():
    allowlist = ["workout.get_progress"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="判断当前计划是否需要调整",
            steps=[MicroPlanStep(
                objective="判断是否需要调整",
                candidate_tools=allowlist,
                execution_strategy="direct",
                success_signal="已经形成调整判断",
            )],
        ),
        decisions=[_decision(
            "complete_step",
            step_summary="现有证据支持形成调整提案",
        )],
        final_response=FinalResponse(
            terminal_action="proposal",
            reply="建议降低频率；这是待确认提案，尚未执行。",
            outcome="adjustment_proposal",
            invocation_metrics=ModelInvocationMetrics(
                input_chars=2200,
                output_chars=96,
                input_tokens=800,
                output_tokens=120,
                finish_reason="stop",
            ),
        ),
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="proposal-contract",
        model=None,
        goal="判断当前计划是否需要调整",
        subtasks=["判断计划适配度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[],
        proposal_creation_enabled=True,
    )

    contract = result.execution_trace.finalization_contract
    assert contract is not None
    assert contract.allowed_outcomes == [
        "adjustment_proposal",
        "no_change_needed",
        "insufficient_evidence",
    ]
    assert contract.selected_outcome == "adjustment_proposal"
    assert contract.derived_terminal_action == "proposal"
    assert policy.finalize_inputs[0]["allowed_outcomes"] == (
        contract.allowed_outcomes
    )
    timing = result.execution_trace.stage_timings[-1]
    assert timing.stage == "finalizer"
    assert timing.input_chars == 2200
    assert timing.output_chars == 96
    assert timing.input_tokens == 800
    assert timing.output_tokens == 120
    assert timing.finish_reason == "stop"


@pytest.mark.asyncio
async def test_low_adherence_narrows_runtime_finalizer_to_proposal():
    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        return {
            "found": True,
            "plan": {"name": "三日入门计划", "days_per_week": 3},
        }

    @tool(
        "workout_get_progress",
        args_schema=WorkoutProgressArguments,
        description="读取训练进度",
    )
    async def progress(weeks: int = 8):
        return {"weeks": weeks, "total_sessions": 1}

    allowlist = ["plan.get_active", "workout.get_progress"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="判断低完成率是否需要调整",
            steps=[MicroPlanStep(
                objective="读取计划频率与四周完成情况",
                candidate_tools=allowlist,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(
                        tool_id="plan.get_active",
                        arguments={},
                    ),
                    PlannedToolAction(
                        tool_id="workout.get_progress",
                        arguments={"weeks": 4},
                    ),
                ],
                success_signal="取得计划与聚合进度",
            )],
        ),
        decisions=[],
        final_response=FinalResponse(
            terminal_action="proposal",
            reply="建议先降为每周一至两练；提案尚未执行，需确认。",
            outcome="adjustment_proposal",
            proposal_draft={
                "proposal_type": "plan_adjustment_v1",
                "changes": [{
                    "change_type": "update_plan_schedule",
                    "stable_display_key": "plan-schedule",
                    "before": {"duration_weeks": 4},
                    "after": {"duration_weeks": 6},
                    "reason": "延长适应周期。",
                    "safety_priority": False,
                }],
                "rationale": ["降低短期完成压力。"],
                "safety_notes": [],
            },
        ),
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-low-adherence",
        run_id="low-adherence-contract",
        model=None,
        goal="最近四周只完成一次，当前计划是否太激进，请给调整建议",
        subtasks=["判断计划适配度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[plan, progress],
        proposal_creation_enabled=True,
    )

    contract = result.execution_trace.finalization_contract
    assert contract is not None
    assert contract.allowed_outcomes == ["adjustment_proposal"]
    assert contract.selected_outcome == "adjustment_proposal"
    assert policy.finalize_inputs[0]["allowed_outcomes"] == [
        "adjustment_proposal"
    ]
    assert result.execution_trace.terminal_action == "proposal"
    assert result.proposal_draft == policy.final_response.proposal_draft
    assert {
        item["tool_id"] for item in result.proposal_observations
    } == {"plan.get_active", "workout.get_progress"}
    assert policy.finalize_inputs[0]["proposal_creation_enabled"] is True


@pytest.mark.asyncio
async def test_high_adherence_narrows_runtime_finalizer_to_no_change():
    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户训练偏好",
    )
    async def profile():
        return {
            "found": True,
            "training_days_per_week": 4,
        }

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        return {
            "found": True,
            "plan": {"name": "四日上下肢计划", "days_per_week": 4},
        }

    @tool(
        "workout_get_progress",
        args_schema=WorkoutProgressArguments,
        description="读取训练进度",
    )
    async def progress(weeks: int = 8):
        return {"weeks": weeks, "total_sessions": 15}

    allowlist = [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="判断高完成率计划是否需要调整",
            steps=[MicroPlanStep(
                objective="读取偏好、计划频率与四周完成情况",
                candidate_tools=allowlist,
                execution_strategy="parallel_read",
                completion_policy="after_all_observations",
                planned_actions=[
                    PlannedToolAction(
                        tool_id="profile.get_summary",
                        arguments={},
                    ),
                    PlannedToolAction(
                        tool_id="plan.get_active",
                        arguments={},
                    ),
                    PlannedToolAction(
                        tool_id="workout.get_progress",
                        arguments={"weeks": 4},
                    ),
                ],
                success_signal="取得偏好、计划与聚合进度",
            )],
        ),
        decisions=[],
        final_response=FinalResponse(
            terminal_action="answer",
            reply="当前四日计划与偏好匹配，四周完成率较高，建议维持。",
            outcome="no_change_needed",
        ),
    )
    result = await execute_planned_agent(
        db=None,
        user_id="user-high-adherence",
        run_id="high-adherence-contract",
        model=None,
        goal="结合最近四周完成情况，判断当前计划是否太激进并给调整建议",
        subtasks=["判断计划适配度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, plan, progress],
    )

    contract = result.execution_trace.finalization_contract
    assert contract is not None
    assert contract.allowed_outcomes == ["no_change_needed"]
    assert contract.selected_outcome == "no_change_needed"
    assert policy.finalize_inputs[0]["allowed_outcomes"] == [
        "no_change_needed"
    ]
    assert result.execution_trace.terminal_action == "answer"


@pytest.mark.asyncio
async def test_plan_fit_fast_path_restores_omitted_evidence_before_execution():
    @tool(
        "profile_get_summary",
        args_schema=NoArguments,
        description="读取用户训练偏好",
    )
    async def profile():
        return {
            "found": True,
            "training_days_per_week": 4,
        }

    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        return {
            "found": True,
            "plan": {"name": "四日上下肢计划", "days_per_week": 4},
        }

    @tool(
        "workout_get_progress",
        args_schema=WorkoutProgressArguments,
        description="读取训练进度",
    )
    async def progress(weeks: int = 8):
        return {"weeks": weeks, "total_sessions": 15}

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史备用证据",
    )
    async def history(limit: int = 20, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        return {"count": 0, "sessions": [], "limit": limit}

    allowlist = [
        "plan.get_active",
        "workout.get_progress",
        "workout.list_history",
        "profile.get_summary",
    ]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="判断当前计划是否太激进",
            steps=[MicroPlanStep(
                objective="读取当前计划",
                candidate_tools=["plan.get_active"],
                execution_strategy="direct",
                completion_policy="after_successful_observation",
                success_signal="当前计划已返回",
            )],
        ),
        decisions=[],
        final_response=FinalResponse(
            terminal_action="answer",
            reply="当前计划与偏好匹配，完成率较高，建议维持。",
            outcome="no_change_needed",
        ),
    )
    shadow_session = ToolRegistryShadowSession(sample_bucket=17)

    result = await execute_planned_agent(
        db=None,
        user_id="user-plan-fit-fast-path",
        run_id="plan-fit-fast-path",
        model=None,
        goal=(
            "结合我最近四周的实际完成情况，看看当前计划是不是太激进，"
            "并给调整建议。"
        ),
        subtasks=["读取偏好、计划和四周进度", "判断计划适配度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[profile, plan, progress, history],
        shadow_session=shadow_session,
    )

    trace = result.execution_trace
    assert len(trace.plan.steps) == 1
    assert trace.plan.steps[0].execution_strategy == "parallel_read"
    assert [
        item.tool_id for item in trace.plan.steps[0].planned_actions
    ] == [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    assert [item.tool_id for item in trace.actions] == [
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    ]
    assert trace.budget_usage.model_calls == 2
    assert not any(
        item.stage == "executor" for item in trace.stage_timings
    )
    assert "planner_fast_path_normalized" in trace.mode_reasons
    assert shadow_session.checks["parallel_policy"].status == "match"


@pytest.mark.asyncio
async def test_plan_progress_fast_path_avoids_executor_before_primary_reads():
    @tool(
        "plan_get_active",
        args_schema=NoArguments,
        description="读取活动计划",
    )
    async def plan():
        return {
            "found": True,
            "plan": {"name": "三日计划", "days_per_week": 3},
        }

    @tool(
        "workout_get_progress",
        args_schema=WorkoutProgressArguments,
        description="读取训练进度",
    )
    async def progress(weeks: int = 8):
        return {"weeks": weeks, "total_sessions": 8}

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史备用证据",
    )
    async def history(limit: int = 20, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        return {"count": 0, "sessions": [], "limit": limit}

    allowlist = [
        "plan.get_active",
        "workout.get_progress",
        "workout.list_history",
    ]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="判断计划适配度，进度失败时使用历史替代",
            steps=[
                MicroPlanStep(
                    objective="读取活动计划",
                    candidate_tools=["plan.get_active"],
                    execution_strategy="direct",
                    completion_policy="after_successful_observation",
                    success_signal="活动计划已返回",
                ),
                MicroPlanStep(
                    objective="读取进度，失败时查询历史",
                    candidate_tools=[
                        "workout.get_progress",
                        "workout.list_history",
                    ],
                    execution_strategy="bounded_react",
                    completion_policy="executor_decides",
                    success_signal="取得进度或历史替代证据",
                ),
            ],
        ),
        decisions=[],
        final_response=FinalResponse(
            terminal_action="answer",
            reply="当前证据支持继续观察。",
            outcome="no_change_needed",
        ),
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-progress-fast-path",
        run_id="progress-fast-path",
        model=None,
        goal=(
            "结合最近四周训练情况判断当前计划是否需要调整；"
            "如果进度统计不可用，"
            "请基于最近训练历史给出保守建议"
        ),
        subtasks=["读取计划和四周进度", "判断计划适配度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[plan, progress, history],
    )

    trace = result.execution_trace
    assert [item.tool_id for item in trace.actions] == [
        "plan.get_active",
        "workout.get_progress",
    ]
    assert not any(
        item.stage == "executor" for item in trace.stage_timings
    )
    assert "planner_fast_path_normalized" in trace.mode_reasons
    assert trace.budget_usage.model_calls == 2
    assert trace.budget_usage.tool_calls == 2


@pytest.mark.asyncio
async def test_finalizer_contract_downgrades_unsolicited_proposal():
    allowlist = ["workout.get_progress"]
    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="查询最近训练进度",
            steps=[MicroPlanStep(
                objective="说明训练进度",
                candidate_tools=allowlist,
                execution_strategy="direct",
                success_signal="已说明训练进度",
            )],
        ),
        decisions=[_decision(
            "complete_step",
            step_summary="训练进度已说明",
        )],
        final_response=FinalResponse(
            terminal_action="proposal",
            reply="未经请求形成调整提案。",
            outcome="adjustment_proposal",
        ),
    )
    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="unsolicited-proposal",
        model=None,
        goal="查询最近训练进度",
        subtasks=["查询进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[],
    )

    assert result.reply == "本轮只完成了查询或评估，没有创建可确认提案。"
    assert result.execution_trace.terminal_action == "answer"
    assert result.proposal_draft is None
    assert "finalizer_unpersisted_proposal_downgraded" in (
        result.execution_trace.mode_reasons
    )
    timing = result.execution_trace.stage_timings[-1]
    assert timing.stage == "finalizer"
    assert timing.status == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_found", "expected_tools"),
    [
        (True, ["workout.get_active_session"]),
        (
            False,
            ["workout.get_active_session", "workout.get_next"],
        ),
    ],
)
async def test_controller_changes_tool_path_from_observation(
    active_found,
    expected_tools,
):
    @tool(
        "workout_get_active_session",
        args_schema=NoArguments,
        description="读取活动训练",
    )
    async def active_session():
        return {"found": active_found}

    @tool(
        "workout_get_next",
        args_schema=NoArguments,
        description="读取下一练",
    )
    async def next_workout():
        return {"found": True, "day_of_week": 5, "exercises": []}

    decisions = [
        _decision(
            "call_tool",
            tool_id="workout.get_active_session",
            arguments={},
        ),
        _decision("complete_step", step_summary="活动训练状态已确认"),
    ]
    if active_found:
        decisions.append(_decision(
            "complete_step",
            step_summary="存在活动训练，无需查询下一练",
        ))
    else:
        decisions.extend([
            _decision(
                "call_tool",
                tool_id="workout.get_next",
                arguments={},
            ),
            _decision("complete_step", step_summary="下一练已确认"),
        ])
    policy = ScriptedPolicy(
        plan=_two_step_plan(
            ["workout.get_active_session"],
            ["workout.get_next"],
        ),
        decisions=decisions,
    )

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id=f"branch-{active_found}",
        model=None,
        goal="判断继续当前训练还是开始下一练",
        subtasks=["检查活动训练", "按观察决定"],
        tool_allowlist=[
            "workout.get_active_session",
            "workout.get_next",
        ],
        initial_trace=_planned_trace([
            "workout.get_active_session",
            "workout.get_next",
        ]),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[active_session, next_workout],
    )

    assert [item.tool_id for item in result.execution_trace.actions] == (
        expected_tools
    )
    assert result.execution_trace.status == "completed"
    assert all(
        item.status == "completed"
        for item in result.execution_trace.plan.steps
    )


@pytest.mark.asyncio
async def test_bounded_react_uses_fallback_tool_after_error():
    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        raise TimeoutError("fixture timeout")

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        return {"count": 2, "sessions": []}

    policy = ScriptedPolicy(
        plan=_two_step_plan(
            ["workout.get_progress", "workout.list_history"],
            ["workout.list_history"],
            first_strategy="bounded_react",
        ),
        decisions=[
            _decision(
                "call_tool",
                tool_id="workout.get_progress",
                arguments={},
            ),
            _decision(
                "call_tool",
                tool_id="workout.list_history",
                arguments={"limit": 4},
            ),
            _decision("complete_step", step_summary="已从历史取得替代证据"),
            _decision("complete_step", step_summary="现有证据足够"),
        ],
    )
    audits: list[ToolAuditEvent] = []

    async def sink(_trace, audit):
        if audit:
            audits.append(audit)

    allowlist = ["workout.get_progress", "workout.list_history"]
    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="bounded-react",
        model=None,
        goal="进度工具失败时使用历史形成建议",
        subtasks=["查询进度", "必要时使用历史"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        event_sink=sink,
        policy=policy,
        tools=[progress, history],
    )

    assert [item.status for item in result.execution_trace.observations] == [
        "error",
        "success",
    ]
    assert [item.status for item in audits] == ["failed", "completed"]
    assert result.execution_trace.budget_usage.tool_calls == 2


@pytest.mark.asyncio
async def test_direct_step_auto_completes_after_successful_observation():
    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        return {"weeks": 4, "total_sessions": 3}

    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="读取最近四周训练进度",
            steps=[MicroPlanStep(
                objective="读取训练进度",
                candidate_tools=["workout.get_progress"],
                execution_strategy="direct",
                completion_policy="after_successful_observation",
                success_signal="获得四周进度观察",
            )],
        ),
        decisions=[_decision(
            "call_tool",
            tool_id="workout.get_progress",
            arguments={},
        )],
    )
    allowlist = ["workout.get_progress"]

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="direct-auto-complete",
        model=None,
        goal="读取最近四周训练进度",
        subtasks=["读取进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[progress],
    )

    trace = result.execution_trace
    assert len(policy.decision_inputs) == 1
    assert trace.plan.steps[0].completion_policy == (
        "after_successful_observation"
    )
    assert trace.plan.steps[0].status == "completed"
    assert trace.budget_usage.model_calls == 3
    assert [item.stage for item in trace.stage_timings] == [
        "planner",
        "executor",
        "finalizer",
    ]
    assert all(item.status == "success" for item in trace.stage_timings)


@pytest.mark.asyncio
async def test_direct_step_error_does_not_auto_complete():
    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        raise TimeoutError("fixture timeout")

    policy = ScriptedPolicy(
        plan=MicroPlan(
            goal="读取最近四周训练进度",
            steps=[MicroPlanStep(
                objective="读取训练进度",
                candidate_tools=["workout.get_progress"],
                execution_strategy="direct",
                completion_policy="after_successful_observation",
                success_signal="获得四周进度观察",
            )],
        ),
        decisions=[
            _decision(
                "call_tool",
                tool_id="workout.get_progress",
                arguments={},
            ),
            _decision(
                "complete_step",
                step_summary="进度工具超时，基于部分证据收口",
            ),
        ],
    )
    allowlist = ["workout.get_progress"]

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="direct-error-no-auto-complete",
        model=None,
        goal="读取最近四周训练进度",
        subtasks=["读取进度"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[progress],
    )

    assert len(policy.decision_inputs) == 2
    assert result.execution_trace.observations[0].status == "error"


@pytest.mark.asyncio
async def test_executor_cannot_upgrade_direct_step_or_repeat_action():
    calls: list[int] = []

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        calls.append(limit)
        return {"count": 1, "sessions": []}

    policy = ScriptedPolicy(
        plan=_two_step_plan(
            ["workout.list_history"],
            ["workout.list_history"],
        ),
        decisions=[
            _decision(
                "call_tool",
                tool_id="workout.list_history",
                arguments={"limit": 4},
            ),
            _decision(
                "call_tool",
                tool_id="workout.list_history",
                arguments={"limit": 4},
            ),
            _decision("complete_step", step_summary="直接步骤已完成"),
            _decision("complete_step", step_summary="不重复查询"),
        ],
    )
    allowlist = ["workout.list_history", "workout.get_progress"]
    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="direct-boundary",
        model=None,
        goal="验证直接步骤边界",
        subtasks=["查询", "判断"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[history],
    )

    assert calls == [4]
    assert len(result.execution_trace.actions) == 1
    assert "工具调用预算已用尽" in (
        policy.decision_inputs[2]["guard_error"]
    )


@pytest.mark.asyncio
async def test_controller_replans_once_and_versions_later_actions():
    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        raise TimeoutError("fixture timeout")

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        return {"count": 3, "sessions": []}

    initial_plan = _two_step_plan(
        ["workout.get_progress"],
        ["workout.list_history"],
    )
    revised_plan = _two_step_plan(
        ["workout.list_history"],
        ["workout.list_history"],
    )
    policy = ScriptedPolicy(
        plan=initial_plan,
        revised_plan=revised_plan,
        decisions=[
            _decision(
                "call_tool",
                tool_id="workout.get_progress",
                arguments={},
            ),
            _decision(
                "request_replan",
                reason="主进度工具超时，需要改用历史记录",
            ),
            _decision(
                "call_tool",
                tool_id="workout.list_history",
                arguments={"limit": 4},
            ),
            _decision("complete_step", step_summary="替代证据已取得"),
            _decision("complete_step", step_summary="调整后的计划已完成"),
        ],
    )
    allowlist = ["workout.get_progress", "workout.list_history"]
    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="replan",
        model=None,
        goal="工具失败后动态调整取证计划",
        subtasks=["查询进度", "形成结论"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[progress, history],
    )

    trace = result.execution_trace
    assert trace.plan.version == 2
    assert trace.plan.revision_reason == "主进度工具超时，需要改用历史记录"
    assert trace.budget_usage.replans == 1
    assert [item.plan_version for item in trace.actions] == [1, 2]
    assert len(policy.replan_inputs) == 1
    assert sum(
        item.stage == "replanner" for item in trace.stage_timings
    ) == 1
    assert sum(
        item.stage == "executor" for item in trace.stage_timings
    ) == 5


@pytest.mark.asyncio
async def test_replanner_deadline_finalizes_with_partial_evidence(monkeypatch):
    history_calls: list[int] = []

    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        raise TimeoutError("fixture timeout")

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        history_calls.append(limit)
        return {"count": 3, "sessions": []}

    plan = _two_step_plan(
        ["workout.get_progress"],
        ["workout.list_history"],
    )
    policy = SlowReplannerPolicy(
        plan=plan,
        decisions=[
            _decision(
                "call_tool",
                tool_id="workout.get_progress",
                arguments={},
            ),
            _decision(
                "request_replan",
                reason="主进度工具超时，需要改用历史记录",
            ),
        ],
        final_response=FinalResponse(
            terminal_action="answer",
            reply="进度查询未完成，本轮没有足够证据形成可靠结论。",
            outcome="insufficient_evidence",
        ),
    )
    allowlist = ["workout.get_progress", "workout.list_history"]
    monkeypatch.setattr(settings, "AGENT_REPLANNER_TIMEOUT_SECONDS", 0.01)

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="replanner-deadline",
        model=None,
        goal="工具失败后查询可用训练证据",
        subtasks=["查询训练进度", "基于可用证据回答"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[progress, history],
    )

    trace = result.execution_trace
    assert result.reply == "进度查询未完成，本轮没有足够证据形成可靠结论。"
    assert trace.status == "completed"
    assert trace.terminal_action == "answer"
    assert trace.termination_reason == "replanner_deadline_exceeded"
    assert [step.status for step in trace.plan.steps] == ["failed", "skipped"]
    assert len(trace.actions) == 1
    assert len(trace.observations) == 1
    assert trace.observations[0].status == "error"
    assert trace.budget_usage.tool_calls == 1
    assert trace.budget_usage.replans == 1
    assert trace.budget_usage.model_calls == 5
    assert history_calls == []
    assert len(policy.replan_inputs) == 1
    assert len(policy.finalize_inputs) == 1
    assert policy.finalize_inputs[0]["observations"][0]["status"] == "error"
    replanner_timing = next(
        item for item in trace.stage_timings if item.stage == "replanner"
    )
    assert replanner_timing.status == "error"
    assert replanner_timing.error_category == "replanner_deadline_exceeded"


@pytest.mark.asyncio
async def test_replanner_deadline_uses_safe_reply_when_finalizer_fails(
    monkeypatch,
):
    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        raise TimeoutError("fixture timeout")

    plan = MicroPlan(
        goal="工具失败后查询可用训练证据",
        steps=[MicroPlanStep(
            objective="查询训练进度",
            candidate_tools=["workout.get_progress"],
            execution_strategy="direct",
            success_signal="取得进度观察",
        )],
    )
    policy = SlowReplannerFailingFinalizerPolicy(
        plan=plan,
        decisions=[
            _decision(
                "call_tool",
                tool_id="workout.get_progress",
                arguments={},
            ),
            _decision("request_replan", reason="进度工具超时"),
        ],
    )
    allowlist = ["workout.get_progress"]
    monkeypatch.setattr(settings, "AGENT_REPLANNER_TIMEOUT_SECONDS", 0.01)

    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="replanner-finalizer-fallback",
        model=None,
        goal="工具失败后查询可用训练证据",
        subtasks=["查询训练进度", "基于可用证据回答"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[progress],
    )

    trace = result.execution_trace
    assert trace.status == "completed"
    assert trace.terminal_action == "answer"
    assert trace.termination_reason == "replanner_deadline_exceeded"
    assert trace.finalization_contract is not None
    assert trace.finalization_contract.selected_outcome == "insufficient_evidence"
    assert "没有修改你的计划或训练记录" in result.reply
    assert trace.budget_usage.model_calls == 5
    finalizer_timing = next(
        item for item in trace.stage_timings if item.stage == "finalizer"
    )
    assert finalizer_timing.status == "error"
    assert finalizer_timing.error_category == "finalizer_provider_error"


@pytest.mark.asyncio
async def test_controller_rejects_a_second_replan_request():
    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        return {"weeks": 4, "total_sessions": 2}

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        return {"count": 2, "sessions": []}

    plan = _two_step_plan(
        ["workout.get_progress"],
        ["workout.list_history"],
    )
    revised = _two_step_plan(
        ["workout.list_history"],
        ["workout.get_progress"],
    )
    policy = ScriptedPolicy(
        plan=plan,
        revised_plan=revised,
        decisions=[
            _decision("request_replan", reason="第一次修订"),
            _decision("request_replan", reason="试图第二次修订"),
            _decision("complete_step", step_summary="基于现有计划继续"),
            _decision("complete_step", step_summary="完成剩余步骤"),
        ],
    )
    allowlist = ["workout.get_progress", "workout.list_history"]
    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id="replan-budget",
        model=None,
        goal="验证重规划硬边界",
        subtasks=["查询", "判断"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[progress, history],
    )

    assert result.execution_trace.budget_usage.replans == 1
    assert len(policy.replan_inputs) == 1
    assert "重规划预算已用尽" in (
        policy.decision_inputs[2]["guard_error"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected_terminal", "expected_risk"),
    [
        (
            _decision(
                "clarify",
                message="请补充你希望比较的时间范围。",
                missing_slot="比较时间范围",
            ),
            "clarify",
            "low",
        ),
        (
            _decision(
                "safe_stop",
                message="请停止训练并及时寻求专业医疗帮助。",
            ),
            "safe_stop",
            "high",
        ),
    ],
)
async def test_executor_can_terminate_with_clarification_or_safe_stop(
    decision,
    expected_terminal,
    expected_risk,
):
    @tool(
        "workout_get_progress",
        args_schema=NoArguments,
        description="读取训练进度",
    )
    async def progress():
        return {"weeks": 4, "total_sessions": 2}

    @tool(
        "workout_list_history",
        args_schema=WorkoutHistoryArguments,
        description="读取训练历史",
    )
    async def history(limit: int = 5, start_date: str = "", end_date: str = "", completed_only: bool = False, statuses: list[str] | None = None):
        return {"count": 2, "sessions": []}

    policy = ScriptedPolicy(
        plan=_two_step_plan(
            ["workout.get_progress"],
            ["workout.list_history"],
        ),
        decisions=[decision],
    )
    allowlist = ["workout.get_progress", "workout.list_history"]
    result = await execute_planned_agent(
        db=None,
        user_id="user-1",
        run_id=f"terminal-{expected_terminal}",
        model=None,
        goal="验证执行中止",
        subtasks=["查询", "判断"],
        tool_allowlist=allowlist,
        initial_trace=_planned_trace(allowlist),
        summarize_observation=_audit_result_summary,
        policy=policy,
        tools=[progress, history],
    )

    assert result.execution_trace.terminal_action == expected_terminal
    assert result.execution_trace.risk_level == expected_risk
    assert all(
        step.status == "skipped"
        for step in result.execution_trace.plan.steps
    )
    if expected_terminal == "clarify":
        assert result.missing_slots == ["比较时间范围"]
