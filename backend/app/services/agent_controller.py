from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.tools import BaseTool

from app.config import settings
from app.schemas.agent_planning import (
    ExecutorDecision,
    FinalizationOutcome,
    FinalResponse,
    MicroPlan,
    MicroPlanStep,
    PlannedToolAction,
)
from app.schemas.agent_trace import (
    AgentActionTrace,
    AgentExecutionTrace,
    AgentFinalizationContractTrace,
    AgentObservationTrace,
    AgentPlanStepTrace,
)
from app.services.agent_planner import (
    ModelPlanningPolicy,
    PlanningModelError,
    build_tool_catalog,
)
from app.services.agent_intent import parse_explicit_plan_adjustment_command
from app.services.agent_structured_errors import safe_error_category
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
)
from app.services.agent_tools import (
    CONDITIONAL_READ_EVIDENCE_GROUPS,
    PARALLEL_READ_CONDITIONAL_TOOL_PAIRS,
    PARALLEL_READ_SAFE_TOOL_IDS,
    ConditionalReadEvidenceGroup,
    TOOL_ID_BY_LANGCHAIN_NAME,
    build_read_tools,
)
from app.services.agent_trace import (
    add_stage_timing,
    observation_fact_keys,
    observation_fingerprint,
)


ObservationSummarizer = Callable[[str, Any], dict[str, Any]]
ParallelToolInvoker = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolAuditEvent:
    call_id: str
    tool_id: str
    arguments: dict[str, Any]
    result_summary: dict[str, Any]
    status: str
    error_code: str | None
    duration_ms: int


@dataclass(frozen=True)
class _ToolInvocationResult:
    tool_id: str
    arguments: dict[str, Any]
    raw_result: Any
    summary: dict[str, Any]
    status: str
    error_code: str | None
    duration_ms: int


@dataclass(frozen=True)
class _RecordedToolBatch:
    trace: AgentExecutionTrace
    raw_observations: list[dict[str, Any]]
    cards: list[dict[str, Any]]
    audits: list[ToolAuditEvent]


TraceEventSink = Callable[
    [AgentExecutionTrace, ToolAuditEvent | None],
    Awaitable[None],
]


@dataclass(frozen=True)
class PlannedExecutionResult:
    reply: str
    execution_trace: AgentExecutionTrace
    cards: list[dict[str, Any]] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    proposal_draft: dict[str, Any] | None = None
    proposal_observations: list[dict[str, Any]] = field(default_factory=list)


class PlanningPolicy(Protocol):
    async def create_plan(self, **kwargs: Any) -> MicroPlan: ...

    async def decide_step(self, **kwargs: Any) -> ExecutorDecision: ...

    async def revise_plan(self, **kwargs: Any) -> MicroPlan: ...

    async def finalize(self, **kwargs: Any) -> FinalResponse: ...


async def _noop_event_sink(
    _trace: AgentExecutionTrace,
    _audit: ToolAuditEvent | None,
) -> None:
    return None


async def _await_policy_stage(
    awaitable: Awaitable[Any],
    *,
    stage: str,
    timeout_seconds: float,
) -> Any:
    try:
        return await asyncio.wait_for(
            awaitable,
            timeout=max(0.001, timeout_seconds),
        )
    except TimeoutError as exc:
        raise PlanningModelError(
            f"Agent {stage} 阶段超过独立时限",
            stage=stage,
            category=f"{stage}_deadline_exceeded",
        ) from exc


def _planning_error_category(error: Exception) -> str:
    if isinstance(error, PlanningModelError):
        return error.category
    return safe_error_category(error)


_PROPOSAL_CAPABLE_MARKERS = (
    "调整",
    "太激进",
    "计划安排问题",
    "越来越少",
    "避开",
    "冲突",
    "降频",
    "提案",
)

_PLAN_FIT_FAST_PATH_TOOL_IDS = frozenset({
    "profile.get_summary",
    "plan.get_active",
    "workout.get_progress",
    "workout.list_history",
})
_PLAN_FIT_PRIMARY_TOOL_IDS = (
    "profile.get_summary",
    "plan.get_active",
    "workout.get_progress",
)
_PLAN_PROGRESS_FAST_PATH_TOOL_IDS = frozenset({
    "plan.get_active",
    "workout.get_progress",
    "workout.list_history",
})
_PLAN_PROGRESS_PRIMARY_TOOL_IDS = (
    "plan.get_active",
    "workout.get_progress",
)
_PLAN_FIT_HISTORY_REQUIRED_MARKERS = (
    "越来越少",
    "下降",
    "趋势",
    "历史",
    "长期变化",
    "持续减少",
)

_DEADLINE_SAFE_REPLY = (
    "我已经取得了部分训练数据，但本轮未能完成可靠分析。"
    "我没有修改你的计划或训练记录，请稍后重试。"
)

_CHINESE_WEEK_COUNTS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


def _deadline_fallback_arguments(
    tool_id: str,
    goal: str,
) -> dict[str, Any]:
    if tool_id != "workout.get_progress":
        return {}
    match = re.search(
        r"(?:最近|近)?\s*(\d{1,2}|十一|十二|[一二三四五六七八九十])\s*周",
        goal,
    )
    if match is None:
        return {}
    raw_weeks = match.group(1)
    weeks = (
        int(raw_weeks)
        if raw_weeks.isdigit()
        else _CHINESE_WEEK_COUNTS.get(raw_weeks)
    )
    if weeks is None:
        return {}
    return {"weeks": min(52, max(1, weeks))}


def _allowed_finalization_outcomes(
    goal: str,
    subtasks: list[str],
) -> list[FinalizationOutcome]:
    semantic_scope = "\n".join([goal, *subtasks])
    if any(marker in semantic_scope for marker in _PROPOSAL_CAPABLE_MARKERS):
        return [
            "adjustment_proposal",
            "no_change_needed",
            "insufficient_evidence",
        ]
    return ["informational_answer", "insufficient_evidence"]


def _latest_successful_observation_result(
    observations: list[dict[str, Any]],
    tool_id: str,
) -> dict[str, Any] | None:
    for observation in reversed(observations):
        result = observation.get("result")
        if (
            observation.get("tool_id") == tool_id
            and observation.get("status") == "success"
            and isinstance(result, dict)
        ):
            return result
    return None


def _valid_non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number < 0:
        return None
    return number


def _has_clear_low_plan_adherence(
    observations: list[dict[str, Any]],
) -> bool:
    """Use complete aggregate evidence only; limited history can undercount."""
    plan_result = _latest_successful_observation_result(
        observations,
        "plan.get_active",
    )
    progress_result = _latest_successful_observation_result(
        observations,
        "workout.get_progress",
    )
    if plan_result is None or progress_result is None:
        return False
    if plan_result.get("found") is not True:
        return False
    plan = plan_result.get("plan")
    if not isinstance(plan, dict):
        return False

    scheduled_days = _valid_non_negative_number(
        plan.get("days_per_week", plan.get("scheduled_days_per_week"))
    )
    weeks = _valid_non_negative_number(progress_result.get("weeks"))
    completed_sessions = _valid_non_negative_number(
        progress_result.get("total_sessions")
    )
    if (
        scheduled_days is None
        or not 1 <= scheduled_days <= 7
        or weeks is None
        or weeks < 2
        or completed_sessions is None
    ):
        return False

    expected_sessions = scheduled_days * weeks
    return (
        expected_sessions >= 4
        and completed_sessions / expected_sessions <= 0.5
    )


def _has_clear_high_plan_fit(
    observations: list[dict[str, Any]],
) -> bool:
    """Require aligned preferences and complete aggregate evidence."""
    supported_tool_ids = {
        "profile.get_summary",
        "plan.get_active",
        "workout.get_progress",
    }
    if any(
        observation.get("status") != "success"
        or observation.get("tool_id") not in supported_tool_ids
        for observation in observations
    ):
        return False

    profile_result = _latest_successful_observation_result(
        observations,
        "profile.get_summary",
    )
    plan_result = _latest_successful_observation_result(
        observations,
        "plan.get_active",
    )
    progress_result = _latest_successful_observation_result(
        observations,
        "workout.get_progress",
    )
    if (
        profile_result is None
        or plan_result is None
        or progress_result is None
        or profile_result.get("found") is not True
        or plan_result.get("found") is not True
    ):
        return False
    plan = plan_result.get("plan")
    if not isinstance(plan, dict):
        return False

    preferred_days = _valid_non_negative_number(
        profile_result.get("training_days_per_week")
    )
    scheduled_days = _valid_non_negative_number(
        plan.get("days_per_week", plan.get("scheduled_days_per_week"))
    )
    weeks = _valid_non_negative_number(progress_result.get("weeks"))
    completed_sessions = _valid_non_negative_number(
        progress_result.get("total_sessions")
    )
    if (
        preferred_days is None
        or not 1 <= preferred_days <= 7
        or scheduled_days is None
        or not 1 <= scheduled_days <= 7
        or preferred_days != scheduled_days
        or weeks is None
        or weeks < 2
        or completed_sessions is None
    ):
        return False

    expected_sessions = scheduled_days * weeks
    return (
        expected_sessions >= 4
        and completed_sessions / expected_sessions >= 0.8
    )


def _finalization_outcomes_for_observations(
    *,
    goal: str,
    subtasks: list[str],
    observations: list[dict[str, Any]],
    proposal_creation_enabled: bool = False,
    force_adjustment_proposal: bool = False,
) -> list[FinalizationOutcome]:
    allowed_outcomes = _allowed_finalization_outcomes(goal, subtasks)
    if (
        not proposal_creation_enabled
        and "adjustment_proposal" in allowed_outcomes
    ):
        allowed_outcomes = [
            "informational_answer",
            *(
                ["no_change_needed"]
                if "no_change_needed" in allowed_outcomes
                else []
            ),
            "insufficient_evidence",
        ]
    active_plan = _latest_successful_observation_result(
        observations,
        "plan.get_active",
    )
    if (
        proposal_creation_enabled
        and (
            force_adjustment_proposal
            or parse_explicit_plan_adjustment_command(goal) is not None
        )
        and active_plan is not None
        and active_plan.get("found") is True
    ):
        return ["adjustment_proposal"]
    if (
        "adjustment_proposal" in allowed_outcomes
        and _has_clear_low_plan_adherence(observations)
    ):
        return ["adjustment_proposal"]
    if (
        "no_change_needed" in allowed_outcomes
        and _has_clear_high_plan_fit(observations)
    ):
        return ["no_change_needed"]
    return allowed_outcomes


def _normalize_plan_fit_fast_path(
    plan: MicroPlan,
    *,
    goal: str,
    subtasks: list[str],
    tool_catalog: list[dict[str, Any]],
) -> tuple[MicroPlan, bool]:
    """Canonicalize server-routed plan-fit primary read routes."""
    available_tool_ids = {
        item["tool_id"]
        for item in tool_catalog
        if isinstance(item.get("tool_id"), str)
    }
    if available_tool_ids == _PLAN_FIT_FAST_PATH_TOOL_IDS:
        primary_tool_ids = _PLAN_FIT_PRIMARY_TOOL_IDS
        history_required_markers = _PLAN_FIT_HISTORY_REQUIRED_MARKERS
    elif available_tool_ids == _PLAN_PROGRESS_FAST_PATH_TOOL_IDS:
        primary_tool_ids = _PLAN_PROGRESS_PRIMARY_TOOL_IDS
        # This route explicitly exposes history as the server-owned fallback
        # for a failed aggregate progress read. The bare word "历史" therefore
        # does not make it an independent primary evidence request; trend and
        # longitudinal language still does.
        history_required_markers = tuple(
            marker
            for marker in _PLAN_FIT_HISTORY_REQUIRED_MARKERS
            if marker != "历史"
        )
    else:
        return plan, False

    semantic_scope = "\n".join([goal, *subtasks])
    if any(
        marker in semantic_scope
        for marker in history_required_markers
    ):
        return plan, False

    planned_tool_ids = {
        action.tool_id
        for step in plan.steps
        for action in step.planned_actions
    }
    if "workout.list_history" in planned_tool_ids:
        return plan, False
    if any(
        step.candidate_tools == ["workout.list_history"]
        for step in plan.steps
    ):
        return plan, False

    progress_arguments = _deadline_fallback_arguments(
        "workout.get_progress",
        goal,
    )
    canonical_actions = [
        PlannedToolAction(
            tool_id=tool_id,
            arguments=(
                progress_arguments
                if tool_id == "workout.get_progress"
                else {}
            ),
        )
        for tool_id in primary_tool_ids
    ]
    if len(plan.steps) == 1:
        step = plan.steps[0]
        if (
            step.execution_strategy == "parallel_read"
            and step.completion_policy == "after_all_observations"
            and step.candidate_tools == list(primary_tool_ids)
            and step.planned_actions == canonical_actions
        ):
            return plan, False

    evidence_label = (
        "三项" if len(primary_tool_ids) == 3 else "两项"
    )
    return MicroPlan(
        goal=goal,
        steps=[MicroPlanStep(
            objective=f"并行取得计划适配所需{evidence_label}主证据",
            candidate_tools=list(primary_tool_ids),
            execution_strategy="parallel_read",
            completion_policy="after_all_observations",
            planned_actions=canonical_actions,
            success_signal=(
                "偏好、计划和聚合进度均已返回"
                if len(primary_tool_ids) == 3
                else "计划和聚合进度均已返回"
            ),
        )],
    ), True


def _validate_final_response_contract(
    response: FinalResponse,
    *,
    allowed_outcomes: list[FinalizationOutcome],
) -> None:
    allowed_actions = {
        (
            "proposal"
            if outcome == "adjustment_proposal"
            else "answer"
        )
        for outcome in allowed_outcomes
    }
    if response.terminal_action not in allowed_actions:
        raise PlanningModelError(
            "Agent 最终动作超出本轮终止动作契约",
            stage="finalizer",
            category="finalization_terminal_action_not_allowed",
        )
    if response.outcome is None:
        return
    if response.outcome not in allowed_outcomes:
        raise PlanningModelError(
            "Agent 最终语义结果超出本轮终止动作契约",
            stage="finalizer",
            category="finalization_outcome_not_allowed",
        )
    expected_action = (
        "proposal"
        if response.outcome == "adjustment_proposal"
        else "answer"
    )
    if response.terminal_action != expected_action:
        raise PlanningModelError(
            "Agent 最终语义结果与终止动作不一致",
            stage="finalizer",
            category="finalization_outcome_action_mismatch",
        )


def _planner_deadline_fallback_plan(
    *,
    goal: str,
    tool_catalog: list[dict[str, Any]],
    max_steps: int,
) -> MicroPlan:
    """Build a small safe plan only after the model Planner times out."""
    tool_by_id = {item["tool_id"]: item for item in tool_catalog}
    progress_alternative_group = {
        "workout.get_progress",
        "workout.list_history",
    }
    progress_has_backup = progress_alternative_group.issubset(tool_by_id)
    observation_dependent_group = {
        "workout.get_active_session",
        "workout.get_next",
    }
    has_observation_dependent_group = observation_dependent_group.issubset(
        tool_by_id
    )
    consumed: set[str] = set()
    steps: list[MicroPlanStep] = []

    # A Planner deadline must not turn independent reads into a long serial
    # Executor chain. Group only generic, side-effect-free primary evidence.
    # Conditional tools stay out; the Controller invokes history only when the
    # primary progress read actually fails.
    independent_items = [
        item for item in tool_catalog
        if item["tool_id"] not in (
            observation_dependent_group
            if has_observation_dependent_group
            else set()
        )
        and not (
            progress_has_backup
            and item["tool_id"] == "workout.list_history"
        )
    ]
    if len(independent_items) >= 2:
        batch_items = independent_items[:3]
        batch_tool_ids = [item["tool_id"] for item in batch_items]
        consumed.update(batch_tool_ids)
        if progress_has_backup and "workout.get_progress" in batch_tool_ids:
            consumed.add("workout.list_history")
        steps.append(MicroPlanStep(
            objective="并行取得 Planner 超时前已路由的独立主证据",
            candidate_tools=batch_tool_ids,
            execution_strategy="parallel_read",
            completion_policy="after_all_observations",
            planned_actions=[
                PlannedToolAction(
                    tool_id=tool_id,
                    arguments=_deadline_fallback_arguments(tool_id, goal),
                )
                for tool_id in batch_tool_ids
            ],
            success_signal="全部独立主证据均已返回",
        ))

    for item in tool_catalog:
        tool_id = item["tool_id"]
        if tool_id in consumed or len(steps) >= max_steps:
            continue
        if (
            progress_has_backup
            and tool_id in progress_alternative_group
        ):
            candidate_tools = [
                candidate
                for candidate in (
                    "workout.get_progress",
                    "workout.list_history",
                )
                if candidate in tool_by_id
            ]
            consumed.update(candidate_tools)
            steps.append(MicroPlanStep(
                objective="读取近期训练进度，失败时使用训练历史作为替代证据",
                candidate_tools=candidate_tools,
                execution_strategy="bounded_react",
                completion_policy="executor_decides",
                success_signal="获得聚合进度或透明记录失败并取得历史替代证据",
            ))
            continue

        if (
            has_observation_dependent_group
            and tool_id in observation_dependent_group
        ):
            candidate_tools = [
                candidate
                for candidate in (
                    "workout.get_active_session",
                    "workout.get_next",
                )
                if candidate in tool_by_id
            ]
            consumed.update(candidate_tools)
            steps.append(MicroPlanStep(
                objective="先检查活动训练，再按观察决定是否查询下一练",
                candidate_tools=candidate_tools,
                execution_strategy="bounded_react",
                completion_policy="executor_decides",
                success_signal="已按活动训练状态取得必要证据",
            ))
            continue

        consumed.add(tool_id)
        description = str(item.get("description") or tool_id).strip()
        steps.append(MicroPlanStep(
            objective=f"取得必要证据：{description}"[:500],
            candidate_tools=[tool_id],
            execution_strategy="direct",
            completion_policy="after_successful_observation",
            success_signal=f"{tool_id} 成功返回观察"[:500],
        ))

    if not steps:
        raise PlanningModelError(
            "Planner 超时后没有可用的安全降级工具",
            stage="planner",
            category="planner_deadline_fallback_unavailable",
        )
    return MicroPlan(goal=goal, steps=steps)


def _tool_map(tools: list[BaseTool]) -> dict[str, BaseTool]:
    return {
        TOOL_ID_BY_LANGCHAIN_NAME.get(tool.name, tool.name): tool
        for tool in tools
    }


def _action_fingerprint(tool_id: str, arguments: dict[str, Any]) -> str:
    normalized = json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{tool_id}:{normalized}"


def _next_sequence(trace: AgentExecutionTrace) -> int:
    sequences = [item.sequence for item in trace.actions]
    sequences.extend(item.sequence for item in trace.observations)
    return max(sequences, default=0) + 1


def _trace_steps(plan: MicroPlan) -> list[AgentPlanStepTrace]:
    return [
        AgentPlanStepTrace(
            id=f"step_{index}",
            objective=step.objective,
            candidate_tools=step.candidate_tools,
            execution_strategy=step.execution_strategy,
            completion_policy=step.completion_policy,
            planned_actions=[
                item.model_dump(mode="python")
                for item in step.planned_actions
            ],
            success_signal=step.success_signal,
        )
        for index, step in enumerate(plan.steps, start=1)
    ]


def _validate_plan_boundary(
    plan: MicroPlan,
    *,
    allowlist: set[str],
    min_steps: int,
    max_steps: int,
    tool_map: dict[str, BaseTool] | None = None,
    max_parallel_actions: int | None = None,
) -> None:
    if not min_steps <= len(plan.steps) <= max_steps:
        raise ValueError("Micro plan step count is outside controller budget")
    parallel_action_count = 0
    for step in plan.steps:
        if not set(step.candidate_tools).issubset(allowlist):
            raise ValueError("Micro plan contains a tool outside the allowlist")
        if step.execution_strategy != "parallel_read":
            continue
        parallel_action_count += len(step.planned_actions)
        action_tool_ids = {
            item.tool_id for item in step.planned_actions
        }
        if not action_tool_ids.issubset(PARALLEL_READ_SAFE_TOOL_IDS):
            raise ValueError("parallel_read contains a non-parallel-safe tool")
        if any(
            pair.issubset(action_tool_ids)
            for pair in PARALLEL_READ_CONDITIONAL_TOOL_PAIRS
        ):
            raise ValueError(
                "parallel_read contains observation-dependent alternatives"
            )
        for action in step.planned_actions:
            tool = (tool_map or {}).get(action.tool_id)
            if tool is None:
                raise ValueError("parallel_read tool is unavailable")
            args_schema = getattr(tool, "args_schema", None)
            if args_schema is not None and hasattr(
                args_schema,
                "model_validate",
            ):
                args_schema.model_validate(action.arguments)
    if (
        max_parallel_actions is not None
        and parallel_action_count > max_parallel_actions
    ):
        raise ValueError("parallel_read actions exceed the tool-call budget")


def _set_step_status(
    trace: AgentExecutionTrace,
    step_index: int,
    status: str,
) -> AgentExecutionTrace:
    steps = list(trace.plan.steps)
    steps[step_index] = steps[step_index].model_copy(update={
        "status": status,
        "status_source": "runtime",
    })
    return trace.model_copy(update={
        "plan": trace.plan.model_copy(update={"steps": steps}),
    })


def _finish_remaining_steps(
    trace: AgentExecutionTrace,
    *,
    current_index: int,
    current_status: str,
) -> AgentExecutionTrace:
    steps: list[AgentPlanStepTrace] = []
    for index, step in enumerate(trace.plan.steps):
        if index < current_index:
            steps.append(step)
        elif index == current_index:
            steps.append(step.model_copy(update={
                "status": current_status,
                "status_source": "runtime",
            }))
        else:
            steps.append(step.model_copy(update={
                "status": "skipped",
                "status_source": "runtime",
            }))
    return trace.model_copy(update={
        "plan": trace.plan.model_copy(update={"steps": steps}),
    })


def _raw_observation_for_prompt(
    *,
    step_id: str,
    call_id: str,
    tool_id: str,
    status: str,
    result: Any,
) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "call_id": call_id,
        "tool_id": tool_id,
        "status": status,
        "result": result,
    }


def _step_payload(step: AgentPlanStepTrace) -> dict[str, Any]:
    return {
        "id": step.id,
        "objective": step.objective,
        "candidate_tools": step.candidate_tools,
        "execution_strategy": step.execution_strategy,
        "completion_policy": step.completion_policy,
        "planned_actions": [
            item.model_dump(mode="python")
            for item in step.planned_actions
        ],
        "success_signal": step.success_signal,
    }


async def _invoke_tool(
    *,
    tool_id: str,
    arguments: dict[str, Any],
    tool_map: dict[str, BaseTool],
    summarize_observation: ObservationSummarizer,
    parallel_tool_invoker: ParallelToolInvoker | None = None,
) -> _ToolInvocationResult:
    started = time.perf_counter()
    status = "success"
    error_code: str | None = None
    try:
        if parallel_tool_invoker is None:
            raw_result = await tool_map[tool_id].ainvoke(arguments)
        else:
            raw_result = await parallel_tool_invoker(tool_id, arguments)
        summary = summarize_observation(tool_id, raw_result)
    except Exception as exc:
        status = "error"
        error_code = type(exc).__name__[:80]
        raw_result = {
            "error": {
                "code": error_code,
                "retryable": isinstance(exc, TimeoutError),
            }
        }
        summary = {"error_code": error_code}
    return _ToolInvocationResult(
        tool_id=tool_id,
        arguments=arguments,
        raw_result=raw_result,
        summary=summary,
        status=status,
        error_code=error_code,
        duration_ms=round((time.perf_counter() - started) * 1000),
    )


def _record_tool_batch(
    trace: AgentExecutionTrace,
    *,
    actions: list[AgentActionTrace],
    results: list[_ToolInvocationResult],
) -> _RecordedToolBatch:
    updated_actions = list(trace.actions)
    observations = list(trace.observations)
    raw_observations: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    audits: list[ToolAuditEvent] = []
    for action, result in zip(actions, results, strict=True):
        action_index = updated_actions.index(action)
        updated_actions[action_index] = action.model_copy(update={
            "status": (
                "completed" if result.status == "success" else "failed"
            ),
        })
        observations.append(AgentObservationTrace(
            sequence=(
                max(
                    [item.sequence for item in updated_actions]
                    + [item.sequence for item in observations],
                    default=0,
                ) + 1
            ),
            action_sequence=action.sequence,
            call_id=action.call_id,
            batch_id=action.batch_id,
            tool_id=result.tool_id,
            status=result.status,
            summary=result.summary,
            result_fingerprint=observation_fingerprint(result.raw_result),
            fact_keys=observation_fact_keys(
                result.tool_id,
                result.summary,
            ),
        ))
        raw_observations.append(_raw_observation_for_prompt(
            step_id=action.step_id or "",
            call_id=action.call_id or "",
            tool_id=result.tool_id,
            status=result.status,
            result=result.raw_result,
        ))
        if result.status == "success":
            cards.append({
                "type": result.tool_id,
                "data": result.raw_result,
            })
        audits.append(ToolAuditEvent(
            call_id=action.call_id or "",
            tool_id=result.tool_id,
            arguments=result.arguments,
            result_summary=result.summary,
            status=(
                "completed" if result.status == "success" else "failed"
            ),
            error_code=result.error_code,
            duration_ms=result.duration_ms,
        ))

    return _RecordedToolBatch(
        trace=trace.model_copy(update={
            "actions": updated_actions,
            "observations": observations,
            "budget_usage": trace.budget_usage.model_copy(update={
                "tool_calls": trace.budget_usage.tool_calls + len(results),
            }),
        }),
        raw_observations=raw_observations,
        cards=cards,
        audits=audits,
    )


def _conditional_fallback_triggered(
    group: ConditionalReadEvidenceGroup,
    primary_result: _ToolInvocationResult,
) -> bool:
    if group.fallback_trigger == "on_error":
        return primary_result.status == "error"
    return (
        primary_result.status == "success"
        and isinstance(primary_result.raw_result, dict)
        and primary_result.raw_result.get("found") is False
    )


def _required_conditional_fallbacks(
    results: list[_ToolInvocationResult],
    *,
    global_allowlist: set[str],
) -> list[ConditionalReadEvidenceGroup]:
    result_by_tool = {item.tool_id: item for item in results}
    required: list[ConditionalReadEvidenceGroup] = []
    for group in CONDITIONAL_READ_EVIDENCE_GROUPS:
        if not {
            group.primary_tool_id,
            group.fallback_tool_id,
        }.issubset(global_allowlist):
            continue
        primary_result = result_by_tool.get(group.primary_tool_id)
        if (
            primary_result is not None
            and group.fallback_tool_id not in result_by_tool
            and _conditional_fallback_triggered(group, primary_result)
        ):
            required.append(group)
    return required


def _routed_evidence_is_covered(
    results: list[_ToolInvocationResult],
    *,
    global_allowlist: set[str],
    required_tool_ids: list[str] | None = None,
) -> bool:
    """Treat a resolved primary/fallback group as one routed evidence source."""
    if not global_allowlist:
        return False
    if required_tool_ids is not None:
        successful = {item.tool_id for item in results if item.status == "success"}
        return set(required_tool_ids).issubset(global_allowlist & successful)

    result_by_tool = {item.tool_id: item for item in results}
    grouped_tool_ids: set[str] = set()
    for group in CONDITIONAL_READ_EVIDENCE_GROUPS:
        group_tool_ids = {
            group.primary_tool_id,
            group.fallback_tool_id,
        }
        if not group_tool_ids.issubset(global_allowlist):
            continue
        grouped_tool_ids.update(group_tool_ids)
        primary_result = result_by_tool.get(group.primary_tool_id)
        if primary_result is None:
            return False
        if _conditional_fallback_triggered(group, primary_result):
            fallback_result = result_by_tool.get(group.fallback_tool_id)
            if fallback_result is None or fallback_result.status != "success":
                return False
        elif primary_result.status != "success":
            return False

    return all(
        (
            tool_id in grouped_tool_ids
            or (
                tool_id in result_by_tool
                and result_by_tool[tool_id].status == "success"
            )
        )
        for tool_id in global_allowlist
    )


def _parallel_actions_are_resolved(
    results: list[_ToolInvocationResult],
    *,
    planned_tool_ids: set[str],
    global_allowlist: set[str],
) -> bool:
    """A failed primary is resolved only by its successful routed fallback."""
    result_by_tool = {item.tool_id: item for item in results}
    groups_by_primary = {
        item.primary_tool_id: item
        for item in CONDITIONAL_READ_EVIDENCE_GROUPS
        if {
            item.primary_tool_id,
            item.fallback_tool_id,
        }.issubset(global_allowlist)
    }
    for tool_id in planned_tool_ids:
        result = result_by_tool.get(tool_id)
        if result is None:
            return False
        group = groups_by_primary.get(tool_id)
        if group is not None and _conditional_fallback_triggered(group, result):
            fallback_result = result_by_tool.get(group.fallback_tool_id)
            if fallback_result is None or fallback_result.status != "success":
                return False
            continue
        if result.status != "success":
            return False
    return True


def _guard_tool_decision(
    decision: ExecutorDecision,
    *,
    step: AgentPlanStepTrace,
    global_allowlist: set[str],
    tool_map: dict[str, BaseTool],
    used_action_fingerprints: set[str],
    remaining_step_tool_calls: int,
    remaining_global_tool_calls: int,
) -> str | None:
    if decision.decision != "call_tool":
        return None
    tool_id = decision.tool_id or ""
    if remaining_step_tool_calls <= 0:
        return "当前步骤工具调用预算已用尽"
    if remaining_global_tool_calls <= 0:
        return "全局工具调用预算已用尽"
    if tool_id not in global_allowlist:
        return "工具不在本轮全局白名单"
    if tool_id not in step.candidate_tools:
        return "工具不在当前步骤候选集"
    if tool_id not in tool_map:
        return "工具当前不可用"
    fingerprint = _action_fingerprint(tool_id, decision.arguments)
    if fingerprint in used_action_fingerprints:
        return "禁止重复相同工具与相同参数"
    return None


async def execute_planned_agent(
    *,
    db: Any,
    user_id: str,
    run_id: str,
    model: Any,
    goal: str,
    subtasks: list[str],
    tool_allowlist: list[str],
    initial_trace: AgentExecutionTrace,
    summarize_observation: ObservationSummarizer,
    event_sink: TraceEventSink | None = None,
    policy: PlanningPolicy | None = None,
    tools: list[BaseTool] | None = None,
    parallel_tool_invoker: ParallelToolInvoker | None = None,
    shadow_session: ToolRegistryShadowSession | None = None,
    proposal_creation_enabled: bool = False,
    force_adjustment_proposal: bool = False,
) -> PlannedExecutionResult:
    """Execute one small linear plan with explicit, planner-owned boundaries."""
    sink = event_sink or _noop_event_sink
    available_tools = (
        tools
        if tools is not None
        else build_read_tools(
            db,
            user_id=user_id,
            allowlist=tool_allowlist,
        )
    )
    tools_by_id = _tool_map(available_tools)
    tool_catalog = build_tool_catalog(available_tools)
    if shadow_session is not None:
        shadow_session.record_constructed_tools(
            available_tools,
            tool_allowlist,
        )
    planning_policy = policy or ModelPlanningPolicy(model)
    global_allowlist = set(tool_allowlist)

    planner_started = time.perf_counter()
    planner_deadline_fallback = False
    planner_fast_path_normalized = False
    try:
        plan = await _await_policy_stage(
            planning_policy.create_plan(
                goal=goal,
                subtasks=subtasks,
                tool_catalog=tool_catalog,
            ),
            stage="planner",
            timeout_seconds=settings.AGENT_PLANNER_TIMEOUT_SECONDS,
        )
        try:
            _validate_plan_boundary(
                plan,
                allowlist=global_allowlist,
                min_steps=1,
                max_steps=settings.AGENT_MAX_PLAN_STEPS,
                tool_map=tools_by_id,
                max_parallel_actions=settings.AGENT_MAX_TOOL_CALLS,
            )
        except Exception:
            if shadow_session is not None:
                shadow_session.record_parallel_policy(
                    tool_allowlist,
                    plan_steps=plan.steps,
                )
            raise
        plan, planner_fast_path_normalized = (
            _normalize_plan_fit_fast_path(
                plan,
                goal=goal,
                subtasks=subtasks,
                tool_catalog=tool_catalog,
            )
        )
        if planner_fast_path_normalized:
            _validate_plan_boundary(
                plan,
                allowlist=global_allowlist,
                min_steps=1,
                max_steps=settings.AGENT_MAX_PLAN_STEPS,
                tool_map=tools_by_id,
                max_parallel_actions=settings.AGENT_MAX_TOOL_CALLS,
            )
        if shadow_session is not None:
            shadow_session.record_parallel_policy(
                tool_allowlist,
                plan_steps=plan.steps,
            )
    except Exception as exc:
        if (
            isinstance(exc, PlanningModelError)
            and exc.category == "planner_deadline_exceeded"
        ):
            plan = _planner_deadline_fallback_plan(
                goal=goal,
                tool_catalog=tool_catalog,
                max_steps=settings.AGENT_MAX_PLAN_STEPS,
            )
            if shadow_session is not None:
                shadow_session.record_parallel_policy(
                    tool_allowlist,
                    plan_steps=plan.steps,
                )
            planner_deadline_fallback = True
        else:
            trace = add_stage_timing(
                initial_trace,
                stage="planner",
                source="model",
                status="error",
                latency_ms=round(
                    (time.perf_counter() - planner_started) * 1000
                ),
                error_category=_planning_error_category(exc),
            )
            await sink(trace, None)
            raise

    trace = initial_trace.model_copy(update={
        "status": "running",
        "plan": initial_trace.plan.model_copy(update={
            "version": 1,
            "goal": goal,
            "planner_source": (
                "deadline_fallback_v1"
                if planner_deadline_fallback
                else "model_micro_plan_v1"
            ),
            "candidate_tools": tool_allowlist,
            "steps": _trace_steps(plan),
            "revision_reason": (
                "planner_deadline_exceeded"
                if planner_deadline_fallback
                else None
            ),
        }),
        "budget_usage": initial_trace.budget_usage.model_copy(update={
            "plan_steps": len(plan.steps),
            "model_calls": 1,
            "tool_calls": 0,
            "replans": 0,
        }),
    })
    trace = add_stage_timing(
        trace,
        stage="planner",
        source="model",
        status="error" if planner_deadline_fallback else "success",
        latency_ms=round((time.perf_counter() - planner_started) * 1000),
        error_category=(
            "planner_deadline_exceeded"
            if planner_deadline_fallback
            else None
        ),
    )
    if planner_deadline_fallback:
        trace = trace.model_copy(update={
            "mode_reasons": [
                *trace.mode_reasons,
                "planner_deadline_fallback",
            ][:8],
        })
    if planner_fast_path_normalized:
        trace = trace.model_copy(update={
            "mode_reasons": [
                *trace.mode_reasons,
                "planner_fast_path_normalized",
            ][:8],
        })
    await sink(trace, None)

    raw_observations: list[dict[str, Any]] = []
    step_summaries: dict[str, str] = {}
    cards: list[dict[str, Any]] = []
    used_action_fingerprints = {
        _action_fingerprint(action.tool_id, action.arguments)
        for action in trace.actions
    }
    step_index = 0
    executor_deadline_hit = False
    replanner_deadline_hit = False
    routed_evidence_covered = False

    while step_index < len(trace.plan.steps):
        step = trace.plan.steps[step_index]
        trace = _set_step_status(trace, step_index, "running")
        await sink(trace, None)
        step_tool_calls = 0
        decision_attempts = 0
        guard_error: str | None = None
        replan_requested = False
        decision_limit = settings.AGENT_MAX_STEP_DECISIONS

        if step.execution_strategy == "parallel_read":
            planned_actions = list(step.planned_actions)
            remaining_global_calls = (
                settings.AGENT_MAX_TOOL_CALLS
                - trace.budget_usage.tool_calls
            )
            fingerprints = [
                _action_fingerprint(item.tool_id, item.arguments)
                for item in planned_actions
            ]
            if len(planned_actions) > remaining_global_calls:
                raise ValueError(
                    "parallel_read actions exceed remaining tool-call budget"
                )
            if any(
                item in used_action_fingerprints for item in fingerprints
            ):
                raise ValueError(
                    "parallel_read repeats an earlier tool action"
                )

            batch_id = f"batch-{run_id[:8]}-{uuid.uuid4()}"
            batch_actions: list[AgentActionTrace] = []
            for item in planned_actions:
                action = AgentActionTrace(
                    sequence=_next_sequence(trace),
                    call_id=f"plan-{run_id[:8]}-{uuid.uuid4()}",
                    batch_id=batch_id,
                    step_id=step.id,
                    plan_version=trace.plan.version,
                    tool_id=item.tool_id,
                    arguments=item.arguments,
                )
                batch_actions.append(action)
                trace = trace.model_copy(update={
                    "actions": [*trace.actions, action],
                })
            await sink(trace, None)

            batch_started = time.perf_counter()
            results = await asyncio.gather(*[
                _invoke_tool(
                    tool_id=item.tool_id,
                    arguments=item.arguments,
                    tool_map=tools_by_id,
                    summarize_observation=summarize_observation,
                    parallel_tool_invoker=parallel_tool_invoker,
                )
                for item in planned_actions
            ])
            recorded_batch = _record_tool_batch(
                trace,
                actions=batch_actions,
                results=results,
            )
            trace = recorded_batch.trace
            raw_observations.extend(recorded_batch.raw_observations)
            cards.extend(recorded_batch.cards)
            for result in results:
                used_action_fingerprints.add(
                    _action_fingerprint(result.tool_id, result.arguments)
                )

            initial_batch_succeeded = all(
                item.status == "success" for item in results
            )
            trace = add_stage_timing(
                trace,
                stage="tool_batch",
                source="controller",
                status=(
                    "success" if initial_batch_succeeded else "error"
                ),
                latency_ms=round(
                    (time.perf_counter() - batch_started) * 1000
                ),
                error_category=(
                    None
                    if initial_batch_succeeded
                    else "parallel_tool_failure"
                ),
            )
            step_tool_calls = len(results)
            for audit in recorded_batch.audits:
                await sink(trace, audit)

            fallback_groups = _required_conditional_fallbacks(
                results,
                global_allowlist=global_allowlist,
            )
            remaining_global_calls = max(
                0,
                settings.AGENT_MAX_TOOL_CALLS
                - trace.budget_usage.tool_calls,
            )
            runnable_fallback_groups: list[
                ConditionalReadEvidenceGroup
            ] = []
            for group in fallback_groups:
                fallback_fingerprint = _action_fingerprint(
                    group.fallback_tool_id,
                    {},
                )
                if (
                    len(runnable_fallback_groups) >= remaining_global_calls
                    or group.fallback_tool_id not in tools_by_id
                    or fallback_fingerprint in used_action_fingerprints
                ):
                    continue
                runnable_fallback_groups.append(group)

            if runnable_fallback_groups:
                fallback_batch_id = (
                    f"fallback-{run_id[:8]}-{uuid.uuid4()}"
                )
                fallback_actions: list[AgentActionTrace] = []
                for group in runnable_fallback_groups:
                    action = AgentActionTrace(
                        sequence=_next_sequence(trace),
                        call_id=f"plan-{run_id[:8]}-{uuid.uuid4()}",
                        batch_id=fallback_batch_id,
                        step_id=step.id,
                        plan_version=trace.plan.version,
                        tool_id=group.fallback_tool_id,
                        arguments={},
                    )
                    fallback_actions.append(action)
                    trace = trace.model_copy(update={
                        "actions": [*trace.actions, action],
                    })
                await sink(trace, None)

                fallback_started = time.perf_counter()
                fallback_results = await asyncio.gather(*[
                    _invoke_tool(
                        tool_id=group.fallback_tool_id,
                        arguments={},
                        tool_map=tools_by_id,
                        summarize_observation=summarize_observation,
                        parallel_tool_invoker=parallel_tool_invoker,
                    )
                    for group in runnable_fallback_groups
                ])
                recorded_fallback = _record_tool_batch(
                    trace,
                    actions=fallback_actions,
                    results=fallback_results,
                )
                trace = recorded_fallback.trace
                raw_observations.extend(
                    recorded_fallback.raw_observations
                )
                cards.extend(recorded_fallback.cards)
                for result in fallback_results:
                    used_action_fingerprints.add(
                        _action_fingerprint(
                            result.tool_id,
                            result.arguments,
                        )
                    )

                fallback_succeeded = all(
                    item.status == "success"
                    for item in fallback_results
                )
                trace = add_stage_timing(
                    trace,
                    stage="tool_batch",
                    source="controller",
                    status=(
                        "success" if fallback_succeeded else "error"
                    ),
                    latency_ms=round(
                        (time.perf_counter() - fallback_started) * 1000
                    ),
                    error_category=(
                        None
                        if fallback_succeeded
                        else "conditional_fallback_failure"
                    ),
                )
                step_tool_calls += len(fallback_results)
                results.extend(fallback_results)
                for audit in recorded_fallback.audits:
                    await sink(trace, audit)

            parallel_actions_resolved = _parallel_actions_are_resolved(
                results,
                planned_tool_ids={
                    item.tool_id for item in planned_actions
                },
                global_allowlist=global_allowlist,
            )
            routed_coverage_satisfied = _routed_evidence_is_covered(
                results,
                global_allowlist=global_allowlist,
                required_tool_ids=(initial_trace.evidence_contract.required_tools
                                   if initial_trace.evidence_contract else None),
            )

            if parallel_actions_resolved:
                step_summaries[step.id] = (
                    f"{len(planned_actions)} 个独立主证据已解析，"
                    f"条件替代调用 {len(results) - len(planned_actions)} 个，"
                    "parallel_read 步骤自动完成"
                )
                trace = _set_step_status(trace, step_index, "completed")
                if (
                    step_index + 1 < len(trace.plan.steps)
                    and routed_coverage_satisfied
                ):
                    for remaining_step in trace.plan.steps[step_index + 1:]:
                        step_summaries[remaining_step.id] = (
                            "前序主证据/条件替代证据已覆盖本轮全部允许证据源，"
                            "跳过不再提供新证据的冗余步骤"
                        )
                    trace = _finish_remaining_steps(
                        trace,
                        current_index=step_index,
                        current_status="completed",
                    )
                    routed_evidence_covered = True
                    await sink(trace, None)
                    break
                await sink(trace, None)
                step_index += 1
                continue

            guard_error = (
                "parallel_read 存在失败观察；本次只允许判断收口或请求重规划，"
                "不得追加工具调用"
            )
            decision_limit = 1

        while decision_attempts < decision_limit:
            if trace.budget_usage.model_calls >= settings.AGENT_MAX_MODEL_CALLS - 1:
                guard_error = "全局模型调用预算已用尽"
                break
            decision_attempts += 1
            if step.execution_strategy == "parallel_read":
                strategy_limit = step_tool_calls
            elif step.execution_strategy == "direct":
                strategy_limit = settings.AGENT_DIRECT_STEP_MAX_TOOL_CALLS
            else:
                strategy_limit = settings.AGENT_REACT_STEP_MAX_TOOL_CALLS
            remaining_step_calls = max(0, strategy_limit - step_tool_calls)
            remaining_global_calls = max(
                0,
                settings.AGENT_MAX_TOOL_CALLS
                - trace.budget_usage.tool_calls,
            )
            step_catalog = [
                item for item in tool_catalog
                if item["tool_id"] in step.candidate_tools
            ]
            executor_started = time.perf_counter()
            try:
                decision = await _await_policy_stage(
                    planning_policy.decide_step(
                        goal=goal,
                        step=_step_payload(step),
                        observations=raw_observations,
                        tool_catalog=step_catalog,
                        remaining_step_tool_calls=remaining_step_calls,
                        remaining_global_tool_calls=remaining_global_calls,
                        guard_error=guard_error,
                    ),
                    stage="executor",
                    timeout_seconds=settings.AGENT_EXECUTOR_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                trace = add_stage_timing(
                    trace,
                    stage="executor",
                    source="model",
                    status="error",
                    latency_ms=round(
                        (time.perf_counter() - executor_started) * 1000
                    ),
                    error_category=_planning_error_category(exc),
                )
                await sink(trace, None)
                if (
                    isinstance(exc, PlanningModelError)
                    and exc.category == "executor_deadline_exceeded"
                ):
                    executor_deadline_hit = True
                    step_summaries[step.id] = (
                        "Executor 超过独立时限，基于现有证据提前收口"
                    )
                    trace = _finish_remaining_steps(
                        trace,
                        current_index=step_index,
                        current_status="failed",
                    )
                    await sink(trace, None)
                    break
                raise
            trace = add_stage_timing(
                trace,
                stage="executor",
                source="model",
                status="success",
                latency_ms=round(
                    (time.perf_counter() - executor_started) * 1000
                ),
            )
            trace = trace.model_copy(update={
                "budget_usage": trace.budget_usage.model_copy(update={
                    "model_calls": trace.budget_usage.model_calls + 1,
                }),
            })

            guard_error = _guard_tool_decision(
                decision,
                step=step,
                global_allowlist=global_allowlist,
                tool_map=tools_by_id,
                used_action_fingerprints=used_action_fingerprints,
                remaining_step_tool_calls=remaining_step_calls,
                remaining_global_tool_calls=remaining_global_calls,
            )
            if guard_error:
                await sink(trace, None)
                continue

            if decision.decision == "complete_step":
                step_summaries[step.id] = decision.step_summary or decision.reason
                trace = _set_step_status(trace, step_index, "completed")
                await sink(trace, None)
                break

            if decision.decision == "request_replan":
                if trace.budget_usage.replans >= settings.AGENT_MAX_REPLANS:
                    guard_error = "重规划预算已用尽，请基于现有证据完成当前步骤"
                    await sink(trace, None)
                    continue
                remaining_plan_steps = (
                    settings.AGENT_MAX_PLAN_STEPS - step_index
                )
                if remaining_plan_steps <= 0:
                    guard_error = "没有剩余计划步骤预算"
                    await sink(trace, None)
                    continue
                replanner_started = time.perf_counter()
                try:
                    revised = await _await_policy_stage(
                        planning_policy.revise_plan(
                            goal=goal,
                            completed_steps=[
                                {
                                    **_step_payload(item),
                                    "summary": step_summaries.get(item.id),
                                }
                                for item in trace.plan.steps[:step_index]
                            ],
                            observations=raw_observations,
                            reason=decision.reason,
                            tool_catalog=tool_catalog,
                            max_steps=remaining_plan_steps,
                        ),
                        stage="replanner",
                        timeout_seconds=(
                            settings.AGENT_REPLANNER_TIMEOUT_SECONDS
                        ),
                    )
                    _validate_plan_boundary(
                        revised,
                        allowlist=global_allowlist,
                        min_steps=1,
                        max_steps=remaining_plan_steps,
                        tool_map=tools_by_id,
                        max_parallel_actions=max(
                            0,
                            settings.AGENT_MAX_TOOL_CALLS
                            - trace.budget_usage.tool_calls,
                        ),
                    )
                except Exception as exc:
                    error_category = _planning_error_category(exc)
                    trace = add_stage_timing(
                        trace,
                        stage="replanner",
                        source="model",
                        status="error",
                        latency_ms=round(
                            (time.perf_counter() - replanner_started) * 1000
                        ),
                        error_category=error_category,
                    )
                    if not (
                        isinstance(exc, PlanningModelError)
                        and exc.category == "replanner_deadline_exceeded"
                    ):
                        await sink(trace, None)
                        raise
                    trace = trace.model_copy(update={
                        "budget_usage": trace.budget_usage.model_copy(update={
                            "model_calls": trace.budget_usage.model_calls + 1,
                            "replans": trace.budget_usage.replans + 1,
                        }),
                    })
                    step_summaries[step.id] = (
                        "Replanner 超过独立时限，停止新增工具调用并基于已有证据收口"
                    )
                    trace = _finish_remaining_steps(
                        trace,
                        current_index=step_index,
                        current_status="failed",
                    )
                    replanner_deadline_hit = True
                    await sink(trace, None)
                    break
                trace = add_stage_timing(
                    trace,
                    stage="replanner",
                    source="model",
                    status="success",
                    latency_ms=round(
                        (time.perf_counter() - replanner_started) * 1000
                    ),
                )
                revised_steps = [
                    AgentPlanStepTrace(
                        id=f"step_{index}",
                        objective=item.objective,
                        candidate_tools=item.candidate_tools,
                        execution_strategy=item.execution_strategy,
                        completion_policy=item.completion_policy,
                        planned_actions=[
                            action.model_dump(mode="python")
                            for action in item.planned_actions
                        ],
                        success_signal=item.success_signal,
                    )
                    for index, item in enumerate(
                        revised.steps,
                        start=step_index + 1,
                    )
                ]
                all_steps = [
                    *trace.plan.steps[:step_index],
                    *revised_steps,
                ]
                trace = trace.model_copy(update={
                    "plan": trace.plan.model_copy(update={
                        "version": trace.plan.version + 1,
                        "steps": all_steps,
                        "revision_reason": decision.reason,
                    }),
                    "budget_usage": trace.budget_usage.model_copy(update={
                        "plan_steps": len(all_steps),
                        "model_calls": trace.budget_usage.model_calls + 1,
                        "replans": trace.budget_usage.replans + 1,
                    }),
                })
                await sink(trace, None)
                replan_requested = True
                break

            if decision.decision in {"clarify", "safe_stop"}:
                terminal_action = (
                    "clarify"
                    if decision.decision == "clarify"
                    else "safe_stop"
                )
                trace = _finish_remaining_steps(
                    trace,
                    current_index=step_index,
                    current_status="skipped",
                ).model_copy(update={
                    "status": "completed",
                    "risk_level": (
                        "high"
                        if terminal_action == "safe_stop"
                        else trace.risk_level
                    ),
                    "terminal_action": terminal_action,
                    "termination_reason": (
                        "executor_clarification_required"
                        if terminal_action == "clarify"
                        else "executor_health_red_flag"
                    ),
                })
                await sink(trace, None)
                return PlannedExecutionResult(
                    reply=decision.message or "当前无法安全继续。",
                    execution_trace=trace,
                    cards=cards,
                    missing_slots=(
                        [decision.missing_slot]
                        if decision.missing_slot
                        else []
                    ),
                    input_tokens=getattr(
                        planning_policy,
                        "input_tokens",
                        None,
                    ),
                    output_tokens=getattr(
                        planning_policy,
                        "output_tokens",
                        None,
                    ),
                )

            tool_id = decision.tool_id or ""
            call_id = f"plan-{run_id[:8]}-{uuid.uuid4()}"
            action_sequence = _next_sequence(trace)
            action = AgentActionTrace(
                sequence=action_sequence,
                call_id=call_id,
                step_id=step.id,
                plan_version=trace.plan.version,
                tool_id=tool_id,
                arguments=decision.arguments,
            )
            trace = trace.model_copy(update={
                "actions": [*trace.actions, action],
            })
            await sink(trace, None)

            started = time.perf_counter()
            status = "success"
            error_code: str | None = None
            try:
                raw_result = await tools_by_id[tool_id].ainvoke(
                    decision.arguments
                )
                summary = summarize_observation(tool_id, raw_result)
                cards.append({"type": tool_id, "data": raw_result})
            except Exception as exc:
                status = "error"
                error_code = type(exc).__name__[:80]
                raw_result = {
                    "error": {
                        "code": error_code,
                        "retryable": isinstance(exc, TimeoutError),
                    }
                }
                summary = {"error_code": error_code}
            duration_ms = round((time.perf_counter() - started) * 1000)

            actions = list(trace.actions)
            actions[-1] = actions[-1].model_copy(update={
                "status": "completed" if status == "success" else "failed",
            })
            observation = AgentObservationTrace(
                sequence=_next_sequence(trace),
                action_sequence=action_sequence,
                call_id=call_id,
                tool_id=tool_id,
                status=status,
                summary=summary,
                result_fingerprint=observation_fingerprint(raw_result),
                fact_keys=observation_fact_keys(tool_id, summary),
            )
            trace = trace.model_copy(update={
                "actions": actions,
                "observations": [*trace.observations, observation],
                "budget_usage": trace.budget_usage.model_copy(update={
                    "tool_calls": trace.budget_usage.tool_calls + 1,
                }),
            })
            step_tool_calls += 1
            used_action_fingerprints.add(
                _action_fingerprint(tool_id, decision.arguments)
            )
            raw_observations.append(_raw_observation_for_prompt(
                step_id=step.id,
                call_id=call_id,
                tool_id=tool_id,
                status=status,
                result=raw_result,
            ))
            await sink(trace, ToolAuditEvent(
                call_id=call_id,
                tool_id=tool_id,
                arguments=decision.arguments,
                result_summary=summary,
                status="completed" if status == "success" else "failed",
                error_code=error_code,
                duration_ms=duration_ms,
            ))

            if (
                status == "success"
                and step.execution_strategy == "direct"
                and step.completion_policy
                == "after_successful_observation"
            ):
                step_summaries[step.id] = (
                    f"{tool_id} 已成功返回观察，direct 步骤自动完成"
                )
                trace = _set_step_status(trace, step_index, "completed")
                await sink(trace, None)
                break

        if replan_requested:
            continue
        if executor_deadline_hit or replanner_deadline_hit:
            break
        if trace.plan.steps[step_index].status != "completed":
            trace = _set_step_status(trace, step_index, "failed")
            step_summaries[step.id] = guard_error or "步骤决策预算已用尽"
            await sink(trace, None)
        step_index += 1

    safe_fallback_required = (
        executor_deadline_hit
        or replanner_deadline_hit
        or trace.budget_usage.model_calls >= settings.AGENT_MAX_MODEL_CALLS
    )
    allowed_outcomes = (
        _allowed_finalization_outcomes(goal, subtasks)
        if safe_fallback_required
        else _finalization_outcomes_for_observations(
            goal=goal,
            subtasks=subtasks,
            observations=raw_observations,
            proposal_creation_enabled=proposal_creation_enabled,
            force_adjustment_proposal=force_adjustment_proposal,
        )
    )
    if force_adjustment_proposal:
        active_plan = _latest_successful_observation_result(
            raw_observations,
            "plan.get_active",
        )
        allowed_outcomes = (
            ["adjustment_proposal"]
            if proposal_creation_enabled
            and active_plan is not None
            and active_plan.get("found") is True
            else ["insufficient_evidence"]
        )
    trace = trace.model_copy(update={
        "finalization_contract": AgentFinalizationContractTrace(
            allowed_outcomes=allowed_outcomes,
        ),
    })
    await sink(trace, None)

    deadline_finalizer_fallback = False
    if force_adjustment_proposal and allowed_outcomes == ["adjustment_proposal"]:
        response = FinalResponse(
            terminal_action="proposal",
            reply="已读取当前训练计划，正在生成待确认的调整提案。",
            outcome="adjustment_proposal",
        )
    elif trace.budget_usage.model_calls >= settings.AGENT_MAX_MODEL_CALLS:
        response = FinalResponse(
            terminal_action="answer",
            reply="我已经完成可用数据的查询，但本轮决策预算已用尽，暂时无法可靠形成完整结论。",
            outcome="insufficient_evidence",
        )
    else:
        finalizer_started = time.perf_counter()
        finalizer_metrics = None
        try:
            finalize_arguments: dict[str, Any] = {
                "goal": goal,
                "steps": [
                    {
                        **_step_payload(step),
                        "status": step.status,
                        "summary": step_summaries.get(step.id),
                    }
                    for step in trace.plan.steps
                ],
                "observations": raw_observations,
                "allowed_outcomes": allowed_outcomes,
            }
            if proposal_creation_enabled:
                finalize_arguments["proposal_creation_enabled"] = True
            response = await planning_policy.finalize(
                **finalize_arguments,
            )
            finalizer_metrics = response.invocation_metrics
            if (
                "adjustment_proposal" not in allowed_outcomes
                and (
                    response.terminal_action == "proposal"
                    or response.outcome == "adjustment_proposal"
                )
            ):
                fallback_outcome = (
                    "informational_answer"
                    if "informational_answer" in allowed_outcomes
                    else allowed_outcomes[0]
                )
                response = response.model_copy(update={
                    "terminal_action": "answer",
                    "outcome": fallback_outcome,
                    "proposal_draft": None,
                    "reply": "本轮只完成了查询或评估，没有创建可确认提案。",
                })
                trace = trace.model_copy(update={
                    "mode_reasons": [
                        *trace.mode_reasons,
                        "finalizer_unpersisted_proposal_downgraded",
                    ][-8:],
                })
            _validate_final_response_contract(
                response,
                allowed_outcomes=allowed_outcomes,
            )
        except Exception as exc:
            trace = add_stage_timing(
                trace,
                stage="finalizer",
                source="model",
                status="error",
                latency_ms=round(
                    (time.perf_counter() - finalizer_started) * 1000
                ),
                error_category=_planning_error_category(exc),
                input_chars=(
                    finalizer_metrics.input_chars
                    if finalizer_metrics is not None else None
                ),
                output_chars=(
                    finalizer_metrics.output_chars
                    if finalizer_metrics is not None else None
                ),
                input_tokens=(
                    finalizer_metrics.input_tokens
                    if finalizer_metrics is not None else None
                ),
                output_tokens=(
                    finalizer_metrics.output_tokens
                    if finalizer_metrics is not None else None
                ),
                finish_reason=(
                    finalizer_metrics.finish_reason
                    if finalizer_metrics is not None else None
                ),
            )
            if not (
                planner_deadline_fallback
                or executor_deadline_hit
                or replanner_deadline_hit
            ):
                await sink(trace, None)
                raise
            deadline_finalizer_fallback = True
            allowed_outcomes = ["insufficient_evidence"]
            trace = trace.model_copy(update={
                "finalization_contract": AgentFinalizationContractTrace(
                    allowed_outcomes=allowed_outcomes,
                ),
                "mode_reasons": [
                    *trace.mode_reasons,
                    "deadline_finalizer_safe_fallback",
                ][:8],
            })
            response = FinalResponse(
                terminal_action="answer",
                reply=_DEADLINE_SAFE_REPLY,
                outcome="insufficient_evidence",
            )
        else:
            trace = add_stage_timing(
                trace,
                stage="finalizer",
                source="model",
                status="success",
                latency_ms=round(
                    (time.perf_counter() - finalizer_started) * 1000
                ),
                input_chars=(
                    finalizer_metrics.input_chars
                    if finalizer_metrics is not None else None
                ),
                output_chars=(
                    finalizer_metrics.output_chars
                    if finalizer_metrics is not None else None
                ),
                input_tokens=(
                    finalizer_metrics.input_tokens
                    if finalizer_metrics is not None else None
                ),
                output_tokens=(
                    finalizer_metrics.output_tokens
                    if finalizer_metrics is not None else None
                ),
                finish_reason=(
                    finalizer_metrics.finish_reason
                    if finalizer_metrics is not None else None
                ),
            )
        trace = trace.model_copy(update={
            "budget_usage": trace.budget_usage.model_copy(update={
                "model_calls": trace.budget_usage.model_calls + 1,
            }),
        })
        await sink(trace, None)

    _validate_final_response_contract(
        response,
        allowed_outcomes=allowed_outcomes,
    )

    partial = any(step.status == "failed" for step in trace.plan.steps)
    finalization_contract = trace.finalization_contract
    if finalization_contract is not None:
        finalization_contract = finalization_contract.model_copy(update={
            "selected_outcome": response.outcome,
            "derived_terminal_action": response.terminal_action,
        })
    trace = trace.model_copy(update={
        "status": "completed",
        "terminal_action": response.terminal_action,
        "termination_reason": (
            "replanner_deadline_exceeded"
            if replanner_deadline_hit
            else (
                "executor_deadline_exceeded"
                if executor_deadline_hit
                else (
                    "planner_deadline_exceeded"
                    if (
                        planner_deadline_fallback
                        and deadline_finalizer_fallback
                    )
                    else (
                        "agent_completed_evidence_covered"
                        if routed_evidence_covered
                        else (
                            "agent_completed_with_partial_evidence"
                            if partial
                            else "agent_completed"
                        )
                    )
                )
            )
        ),
        "finalization_contract": finalization_contract,
    })
    await sink(trace, None)
    return PlannedExecutionResult(
        reply=response.reply,
        execution_trace=trace,
        cards=cards,
        input_tokens=getattr(planning_policy, "input_tokens", None),
        output_tokens=getattr(planning_policy, "output_tokens", None),
        proposal_draft=(
            response.proposal_draft if proposal_creation_enabled else None
        ),
        proposal_observations=(
            raw_observations if proposal_creation_enabled else []
        ),
    )
