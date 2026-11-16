from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalDraft,
)


ExecutionStrategy = Literal["direct", "parallel_read", "bounded_react"]
CompletionPolicy = Literal[
    "executor_decides",
    "after_successful_observation",
    "after_all_observations",
]
FinalizationOutcome = Literal[
    "informational_answer",
    "no_change_needed",
    "insufficient_evidence",
    "adjustment_proposal",
]
ModelFinishReason = Literal[
    "stop",
    "length",
    "tool_calls",
    "content_filter",
    "other",
]


class PlannedToolAction(BaseModel):
    """One Planner-owned, argument-complete action in a parallel read batch."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


class MicroPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=500)
    candidate_tools: list[str] = Field(min_length=1, max_length=4)
    execution_strategy: ExecutionStrategy
    completion_policy: CompletionPolicy = "executor_decides"
    planned_actions: list[PlannedToolAction] = Field(
        default_factory=list,
        max_length=3,
    )
    success_signal: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_candidate_tools(self) -> MicroPlanStep:
        if len(self.candidate_tools) != len(set(self.candidate_tools)):
            raise ValueError("step candidate tools must be unique")
        if (
            self.completion_policy == "after_successful_observation"
            and self.execution_strategy != "direct"
        ):
            raise ValueError(
                "automatic completion is only valid for direct steps"
            )
        if self.execution_strategy == "parallel_read":
            if not 2 <= len(self.planned_actions) <= 3:
                raise ValueError(
                    "parallel_read requires two or three planned actions"
                )
            action_tool_ids = [item.tool_id for item in self.planned_actions]
            if len(action_tool_ids) != len(set(action_tool_ids)):
                raise ValueError("parallel_read action tools must be unique")
            if action_tool_ids != self.candidate_tools:
                raise ValueError(
                    "parallel_read planned actions must match candidate tools"
                )
            if self.completion_policy != "after_all_observations":
                raise ValueError(
                    "parallel_read requires after_all_observations"
                )
        elif self.planned_actions:
            raise ValueError(
                "planned actions are only valid for parallel_read steps"
            )
        if (
            self.completion_policy == "after_all_observations"
            and self.execution_strategy != "parallel_read"
        ):
            raise ValueError(
                "after_all_observations is only valid for parallel_read"
            )
        return self


class MicroPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    steps: list[MicroPlanStep] = Field(min_length=1, max_length=3)


class MicroPlanDraft(BaseModel):
    """Planner-owned fields; the trusted goal is attached by the server."""

    model_config = ConfigDict(extra="forbid")

    steps: list[MicroPlanStep] = Field(min_length=1, max_length=3)


class ExecutorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal[
        "call_tool",
        "complete_step",
        "request_replan",
        "clarify",
        "safe_stop",
    ]
    tool_id: str | None = Field(default=None, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    step_summary: str | None = Field(default=None, max_length=1000)
    reason: str = Field(min_length=1, max_length=500)
    message: str | None = Field(default=None, max_length=1000)
    missing_slot: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_decision_payload(self) -> ExecutorDecision:
        if self.decision == "call_tool":
            if not self.tool_id:
                raise ValueError("call_tool requires tool_id")
        elif self.tool_id is not None or self.arguments:
            raise ValueError("only call_tool may include tool_id or arguments")

        if self.decision == "complete_step" and not self.step_summary:
            raise ValueError("complete_step requires step_summary")
        if self.decision == "clarify" and (
            not self.message or not self.missing_slot
        ):
            raise ValueError("clarify requires message and missing_slot")
        if self.decision == "safe_stop" and not self.message:
            raise ValueError("safe_stop requires message")
        return self


class ModelInvocationMetrics(BaseModel):
    """Content-size and provider usage counters without prompt contents."""

    model_config = ConfigDict(extra="forbid")

    input_chars: int = Field(ge=0)
    output_chars: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    finish_reason: ModelFinishReason | None = None


class FinalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_action: Literal["answer", "proposal"]
    reply: str = Field(min_length=1, max_length=8000)
    outcome: FinalizationOutcome | None = None
    proposal_draft: dict[str, Any] | None = Field(default=None, exclude=True)
    invocation_metrics: ModelInvocationMetrics | None = Field(
        default=None,
        exclude=True,
    )


class FinalizationDecision(BaseModel):
    """Model-owned semantic result; Controller owns terminal action mapping."""

    model_config = ConfigDict(extra="forbid")

    outcome: FinalizationOutcome
    reply: str = Field(min_length=1, max_length=8000)


class ProposalFinalizationDecision(FinalizationDecision):
    """Opt-in Finalizer shape used only while proposal creation is enabled."""

    proposal_draft: PlanAdjustmentProposalDraft | None = None

    @model_validator(mode="after")
    def validate_outcome_draft_pair(self) -> ProposalFinalizationDecision:
        if (
            self.outcome == "adjustment_proposal"
            and self.proposal_draft is None
        ):
            raise ValueError(
                "adjustment_proposal requires proposal_draft"
            )
        if (
            self.outcome != "adjustment_proposal"
            and self.proposal_draft is not None
        ):
            raise ValueError(
                "only adjustment_proposal may include proposal_draft"
            )
        return self
