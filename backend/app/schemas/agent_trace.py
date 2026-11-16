from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalCreationReasonCode,
)
from app.schemas.agent_tool_registry import ToolRegistryShadowReport
from app.schemas.agent_task import TaskSnapshot


ExecutionMode = Literal["direct", "planned", "clarify", "safe_stop"]
RuntimeTerminalAction = Literal[
    "answer",
    "proposal",
    "clarify",
    "safe_stop",
    "failed",
]
AgentStage = Literal[
    "intent",
    "planner",
    "executor",
    "replanner",
    "finalizer",
    "direct_agent",
    "tool_batch",
]
FinalizationOutcomeTrace = Literal[
    "informational_answer",
    "no_change_needed",
    "insufficient_evidence",
    "adjustment_proposal",
]
ProposalPersistenceStatusTrace = Literal[
    "not_attempted",
    "created",
    "replayed",
    "rejected",
    "failed",
]
ProposalPersistenceReasonTrace = Literal[
    "feature_disabled",
    "run_ownership_lost",
    "proposal_idempotency_conflict",
]


class AgentPlannedToolActionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentPlanStepTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^step_[1-9][0-9]*$", max_length=30)
    objective: str = Field(min_length=1, max_length=500)
    depends_on: list[str] = Field(default_factory=list, max_length=6)
    candidate_tools: list[str] = Field(default_factory=list, max_length=4)
    execution_strategy: Literal[
        "direct",
        "parallel_read",
        "bounded_react",
        "agent_loop",  # Legacy trace compatibility; never emitted by v1 planner.
    ]
    completion_policy: Literal[
        "executor_decides",
        "after_successful_observation",
        "after_all_observations",
    ] = "executor_decides"
    planned_actions: list[AgentPlannedToolActionTrace] = Field(
        default_factory=list,
        max_length=3,
    )
    success_signal: str | None = Field(default=None, max_length=500)
    status: Literal[
        "pending",
        "running",
        "completed",
        "failed",
        "skipped",
    ] = "pending"
    status_source: Literal["runtime", "inferred"] = "runtime"


class AgentPlanTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    goal: str = Field(min_length=1, max_length=4000)
    planner_source: Literal[
        "direct_v1",
        "intent_subtasks_v1",
        "planning_gate_v1",
        "model_micro_plan_v1",
        "deadline_fallback_v1",
        "clarification_gate_v1",
        "safety_gate_v1",
    ]
    candidate_tools: list[str] = Field(default_factory=list, max_length=8)
    steps: list[AgentPlanStepTrace] = Field(default_factory=list, max_length=6)
    revision_reason: str | None = Field(default=None, max_length=500)


class AgentActionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    action_type: Literal["call_tool"] = "call_tool"
    call_id: str | None = Field(default=None, max_length=120)
    batch_id: str | None = Field(default=None, max_length=120)
    step_id: str | None = Field(default=None, max_length=30)
    plan_version: int = Field(default=1, ge=1)
    tool_id: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: Literal["requested", "completed", "failed"] = "requested"


class AgentObservationTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    action_sequence: int = Field(ge=1)
    call_id: str | None = Field(default=None, max_length=120)
    batch_id: str | None = Field(default=None, max_length=120)
    tool_id: str = Field(min_length=1, max_length=100)
    status: Literal["success", "error"]
    summary: dict[str, Any] = Field(default_factory=dict)
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    fact_keys: list[str] = Field(default_factory=list, max_length=30)


class AgentBudgetUsageTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_steps: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    replans: int = Field(default=0, ge=0)


class AgentStageTimingTrace(BaseModel):
    """Privacy-safe timing and size metrics for one stage invocation."""

    model_config = ConfigDict(extra="forbid")

    stage: AgentStage
    attempt: int = Field(ge=0)
    source: Literal["model", "rules", "controller"]
    status: Literal["success", "error"]
    latency_ms: int = Field(ge=0)
    error_category: str | None = Field(default=None, max_length=160)
    transport: str | None = Field(default=None, max_length=80)
    input_chars: int | None = Field(default=None, ge=0)
    output_chars: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    finish_reason: Literal[
        "stop",
        "length",
        "tool_calls",
        "content_filter",
        "other",
    ] | None = None


class AgentFinalizationContractTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed_outcomes: list[FinalizationOutcomeTrace] = Field(
        min_length=1,
        max_length=4,
    )
    selected_outcome: FinalizationOutcomeTrace | None = None
    derived_terminal_action: Literal["answer", "proposal"] | None = None


class AgentProposalCreationTrace(BaseModel):
    """Privacy-safe result of the optional Proposal creation boundary."""

    model_config = ConfigDict(extra="forbid")

    eligible: bool
    reason_code: PlanAdjustmentProposalCreationReasonCode | None = None
    persisted: bool = False
    persistence_status: ProposalPersistenceStatusTrace = "not_attempted"
    persistence_reason_code: ProposalPersistenceReasonTrace | None = None

    @model_validator(mode="after")
    def validate_lifecycle_projection(self) -> Self:
        if self.eligible == (self.reason_code is not None):
            raise ValueError(
                "eligible proposal diagnostics cannot include a build reason"
            )
        if not self.eligible and (
            self.persisted
            or self.persistence_status != "not_attempted"
            or self.persistence_reason_code is not None
        ):
            raise ValueError(
                "rejected proposal builds cannot report persistence"
            )
        persisted_status = self.persistence_status in {"created", "replayed"}
        if self.persisted != persisted_status:
            raise ValueError(
                "persisted must match created or replayed persistence status"
            )
        if (
            self.persistence_status == "rejected"
        ) != (self.persistence_reason_code is not None):
            raise ValueError(
                "rejected persistence requires only a stable reason code"
            )
        return self


class AgentEvidenceContractTrace(BaseModel):
    """Additive task contract; absence means a legacy/unmeasured Run."""
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    required_tools: list[str] = Field(max_length=14)
    missing_tools: list[str] = Field(default_factory=list, max_length=14)
    status: Literal["pending", "complete", "partial", "blocked"] = "pending"
    reason: Literal["budget_exceeded", "authority_missing", "observations_missing"] | None = None


class AgentRequestContextTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"


class AgentExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_version: Literal["1.0", "1.1", "1.2"] = "1.0"
    router_version: str = Field(default="semantic_route_v2", max_length=40)
    execution_mode: ExecutionMode
    risk_level: Literal["low", "medium", "high"]
    mode_reasons: list[str] = Field(default_factory=list, max_length=8)
    status: Literal["running", "completed", "failed"] = "running"
    plan: AgentPlanTrace
    actions: list[AgentActionTrace] = Field(default_factory=list, max_length=20)
    observations: list[AgentObservationTrace] = Field(
        default_factory=list,
        max_length=20,
    )
    stage_timings: list[AgentStageTimingTrace] = Field(
        default_factory=list,
        max_length=40,
    )
    finalization_contract: AgentFinalizationContractTrace | None = None
    terminal_action: RuntimeTerminalAction | None = None
    termination_reason: str | None = Field(default=None, max_length=100)
    budget_usage: AgentBudgetUsageTrace = Field(
        default_factory=AgentBudgetUsageTrace
    )
    tool_registry_shadow: ToolRegistryShadowReport | None = None
    proposal_creation: AgentProposalCreationTrace | None = None
    evidence_contract: AgentEvidenceContractTrace | None = None
    request_context: AgentRequestContextTrace | None = None
    task_state: TaskSnapshot | None = None

    @model_validator(mode="after")
    def validate_optional_trace_versions(self) -> Self:
        if (
            self.tool_registry_shadow is not None
            and self.trace_version == "1.0"
        ):
            raise ValueError(
                "tool registry shadow requires trace version 1.1 or later"
            )
        if (
            self.proposal_creation is not None
            and self.trace_version != "1.2"
        ):
            raise ValueError(
                "proposal creation diagnostics require trace version 1.2"
            )
        return self

    @model_serializer(mode="wrap")
    def omit_inactive_optional_fields(
        self,
        handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        serialized = handler(self)
        if self.tool_registry_shadow is None:
            serialized.pop("tool_registry_shadow", None)
        if self.proposal_creation is None:
            serialized.pop("proposal_creation", None)
        if self.evidence_contract is None:
            serialized.pop("evidence_contract", None)
        if self.request_context is None:
            serialized.pop("request_context", None)
        if self.task_state is None:
            serialized.pop("task_state", None)
        return serialized
