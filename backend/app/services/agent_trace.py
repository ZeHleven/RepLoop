from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from app.schemas.agent_trace import (
    AgentActionTrace,
    AgentBudgetUsageTrace,
    AgentExecutionTrace,
    AgentObservationTrace,
    AgentPlanStepTrace,
    AgentPlanTrace,
    AgentStage,
    AgentStageTimingTrace,
    ExecutionMode,
    RuntimeTerminalAction,
)
from app.services.agent_intent import (
    IntentResolution,
    IntentResolverOutcome,
    is_explicit_plan_adjustment_resolution,
)
from app.services.agent_tools import TOOL_ID_BY_LANGCHAIN_NAME


ObservationSummarizer = Callable[[str, Any], dict[str, Any]]


def add_stage_timing(
    trace: AgentExecutionTrace,
    *,
    stage: AgentStage,
    source: str,
    status: str,
    latency_ms: int,
    error_category: str | None = None,
    attempt: int | None = None,
    input_chars: int | None = None,
    output_chars: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    finish_reason: str | None = None,
) -> AgentExecutionTrace:
    """Append one bounded, privacy-safe timing event to an execution trace."""
    if attempt is None:
        attempt = 1 + max(
            (
                item.attempt
                for item in trace.stage_timings
                if item.stage == stage
            ),
            default=0,
        )
    timing = AgentStageTimingTrace(
        stage=stage,
        attempt=attempt,
        source=source,
        status=status,
        latency_ms=max(0, latency_ms),
        error_category=(error_category[:160] if error_category else None),
        input_chars=input_chars,
        output_chars=output_chars,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        finish_reason=finish_reason,
    )
    return trace.model_copy(update={
        "stage_timings": [*trace.stage_timings, timing],
    })


def _intent_stage_timings(
    outcome: IntentResolverOutcome | None,
) -> list[AgentStageTimingTrace]:
    if outcome is None:
        return []
    timings = [
        AgentStageTimingTrace(
            stage="intent",
            attempt=item.attempt,
            source="model",
            status=item.status,
            latency_ms=item.latency_ms,
            error_category=item.error_category,
            transport=item.transport,
            output_chars=item.output_chars,
            finish_reason=(
                item.finish_reason
                if item.finish_reason in {
                    "stop", "length", "tool_calls", "content_filter", "other"
                }
                else "other" if item.finish_reason else None
            ),
        )
        for item in outcome.attempt_timings
    ]
    if outcome.source == "rules":
        # A zero-cost rules event after failed model attempts makes the final
        # fallback source explicit without double-counting model wall time.
        rules_latency = outcome.latency_ms if not timings else 0
        timings.append(AgentStageTimingTrace(
            stage="intent",
            attempt=0,
            source="rules",
            status="success",
            latency_ms=rules_latency,
            error_category=None,
        ))
    return timings


def observation_fingerprint(content: Any) -> str:
    if isinstance(content, str):
        try:
            normalized: Any = json.loads(content)
        except (TypeError, ValueError):
            normalized = content
    else:
        normalized = content
    serialized = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def select_execution_mode(
    resolution: IntentResolution,
    tool_allowlist: list[str],
    *,
    proposal_creation_enabled: bool = False,
) -> tuple[ExecutionMode, list[str]]:
    if resolution.risk_level == "high":
        return "safe_stop", ["health_red_flag"]
    if resolution.clarification_required:
        return "clarify", ["critical_information_missing"]

    if (
        proposal_creation_enabled
        and is_explicit_plan_adjustment_resolution(resolution)
        and tool_allowlist == ["plan.get_active"]
    ):
        return "planned", ["explicit_plan_adjustment_proposal"]

    from app.services.agent_query_reports import report_for_resolution
    if report_for_resolution(resolution, tool_allowlist) is not None:
        return "direct", ["verified_query_report"]

    reasons: list[str] = []
    if len(tool_allowlist) > 1 and len(resolution.subtasks) > 1:
        reasons.extend([
            "multiple_evidence_sources",
            "multiple_semantic_subtasks",
        ])
        return "planned", reasons
    return "direct", ["single_goal_or_tool"]


def build_initial_execution_trace(
    resolution: IntentResolution,
    tool_allowlist: list[str],
    intent_outcome: IntentResolverOutcome | None = None,
    *,
    proposal_creation_enabled: bool = False,
) -> AgentExecutionTrace:
    execution_mode, mode_reasons = select_execution_mode(
        resolution,
        tool_allowlist,
        proposal_creation_enabled=proposal_creation_enabled,
    )
    goal = resolution.resolved_query.strip() or "完成当前用户请求"

    if execution_mode == "safe_stop":
        planner_source = "safety_gate_v1"
        steps: list[AgentPlanStepTrace] = []
    elif execution_mode == "clarify":
        planner_source = "clarification_gate_v1"
        steps = []
    elif execution_mode == "direct":
        planner_source = "direct_v1"
        steps = [AgentPlanStepTrace(
            id="step_1",
            objective=goal[:500],
            candidate_tools=tool_allowlist,
            execution_strategy="direct",
            success_signal="完成当前单一目标",
        )]
    else:
        # The gate deliberately does not pretend that semantic subtasks are a
        # real plan. The model Planner replaces this empty plan before execute.
        planner_source = "planning_gate_v1"
        steps = []

    plan = AgentPlanTrace(
        goal=goal,
        planner_source=planner_source,
        candidate_tools=tool_allowlist,
        steps=steps,
    )
    return AgentExecutionTrace(
        router_version="semantic_route_v2",
        execution_mode=execution_mode,
        risk_level=resolution.risk_level,
        mode_reasons=mode_reasons,
        plan=plan,
        stage_timings=_intent_stage_timings(intent_outcome),
        budget_usage=AgentBudgetUsageTrace(plan_steps=len(steps)),
    )


def observation_fact_keys(tool_id: str, summary: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for key, value in summary.items():
        if key == "fields_returned" and isinstance(value, list):
            keys.extend(
                f"{tool_id}.{field}"
                for field in value
                if isinstance(field, str)
            )
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            keys.append(f"{tool_id}.{key}")
    return list(dict.fromkeys(keys))[:30]


def complete_execution_trace(
    trace: AgentExecutionTrace,
    result: dict[str, Any],
    *,
    summarize_observation: ObservationSummarizer,
    terminal_action: RuntimeTerminalAction = "answer",
    termination_reason: str = "agent_completed",
) -> AgentExecutionTrace:
    messages = result.get("messages")
    if not isinstance(messages, list):
        messages = []

    actions: list[dict[str, Any]] = []
    action_by_call_id: dict[str, dict[str, Any]] = {}
    observations: list[AgentObservationTrace] = []
    sequence = 0
    model_calls = 0
    deterministic = result.get("response_mode", "").startswith("verified_")

    for message in messages:
        if getattr(message, "type", None) == "ai" and not deterministic:
            model_calls += 1
        calls = getattr(message, "tool_calls", None)
        if isinstance(calls, list):
            for call in calls:
                if not isinstance(call, dict):
                    continue
                langchain_name = call.get("name")
                if not isinstance(langchain_name, str):
                    continue
                sequence += 1
                call_id = (
                    call.get("id") if isinstance(call.get("id"), str) else None
                )
                arguments = call.get("args")
                action = {
                    "sequence": sequence,
                    "call_id": call_id,
                    "tool_id": TOOL_ID_BY_LANGCHAIN_NAME.get(
                        langchain_name,
                        langchain_name,
                    ),
                    "arguments": (
                        arguments
                        if isinstance(arguments, dict)
                        else {"value": arguments}
                    ),
                    "status": "requested",
                }
                actions.append(action)
                if call_id:
                    action_by_call_id[call_id] = action

        if getattr(message, "type", None) != "tool":
            continue
        call_id = getattr(message, "tool_call_id", None)
        action = (
            action_by_call_id.get(call_id)
            if isinstance(call_id, str)
            else None
        )
        if action is None:
            continue
        raw_status = getattr(message, "status", None)
        observation_status = "error" if raw_status == "error" else "success"
        action["status"] = (
            "failed" if observation_status == "error" else "completed"
        )
        sequence += 1
        summary = summarize_observation(
            action["tool_id"],
            getattr(message, "content", ""),
        )
        observations.append(AgentObservationTrace(
            sequence=sequence,
            action_sequence=action["sequence"],
            call_id=call_id,
            tool_id=action["tool_id"],
            status=observation_status,
            summary=summary,
            result_fingerprint=observation_fingerprint(
                getattr(message, "content", "")
            ),
            fact_keys=observation_fact_keys(action["tool_id"], summary),
        ))

    completed_steps = [
        step.model_copy(update={
            "status": "completed",
            "status_source": "runtime" if deterministic else "inferred",
        })
        for step in trace.plan.steps
    ]
    return trace.model_copy(update={
        "status": "completed",
        "plan": trace.plan.model_copy(update={"steps": completed_steps}),
        "actions": [AgentActionTrace.model_validate(item) for item in actions],
        "observations": observations,
        "terminal_action": terminal_action,
        "termination_reason": result.get("response_mode", termination_reason),
        "budget_usage": trace.budget_usage.model_copy(update={
            "model_calls": model_calls,
            "tool_calls": len(actions),
        }),
    })


def terminate_execution_trace(
    trace: AgentExecutionTrace,
    *,
    terminal_action: RuntimeTerminalAction,
    termination_reason: str,
    failed: bool = False,
) -> AgentExecutionTrace:
    step_status = "failed" if failed else "completed"
    finished_steps = []
    for step in trace.plan.steps:
        if step.status in {"completed", "skipped"}:
            finished_steps.append(step)
        else:
            finished_steps.append(step.model_copy(update={
                "status": step_status,
                "status_source": "runtime",
            }))
    return trace.model_copy(update={
        "status": "failed" if failed else "completed",
        "plan": trace.plan.model_copy(update={"steps": finished_steps}),
        "terminal_action": terminal_action,
        "termination_reason": termination_reason,
    })
