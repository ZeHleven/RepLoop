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


class AgentCountEvidenceSubstitutionTrace(BaseModel):
    """Server-verified equivalent count evidence, not a fabricated tool call."""
    model_config = ConfigDict(extra="forbid")
    required_tool: Literal["workout.get_progress"] = "workout.get_progress"
    observed_tool: Literal["workout.list_history"] = "workout.list_history"
    proof_type: Literal["complete_training_session_count"] = "complete_training_session_count"
    call_id: str = Field(min_length=1, max_length=120)
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    range_start: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    range_end: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    statuses: list[str] = Field(min_length=1, max_length=5)
    session_count: int = Field(ge=0)


class AgentNutritionEvidenceSubstitutionTrace(BaseModel):
    """Complete stored date records from another attested nutrition source."""
    model_config = ConfigDict(extra="forbid")
    required_tool: Literal["nutrition.list_history", "nutrition.get_today"]
    observed_tool: Literal["nutrition.list_history", "nutrition.get_today", "nutrition.get_recent_context"]
    proof_type: Literal["complete_recorded_nutrition_dates"] = "complete_recorded_nutrition_dates"
    call_id: str = Field(min_length=1, max_length=120)
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_dates: list[str] = Field(min_length=1, max_length=366)
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    scope_quotes: list[str] = Field(min_length=1, max_length=8)
    coverage: Literal["all_stored_meals_and_items"] = "all_stored_meals_and_items"
    actual_intake_completeness: Literal["unknown"] = "unknown"


class AgentEvidenceContractTrace(BaseModel):
    """Additive task contract; absence means a legacy/unmeasured Run."""
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    required_tools: list[str] = Field(max_length=14)
    missing_tools: list[str] = Field(default_factory=list, max_length=14)
    status: Literal["pending", "complete", "partial", "blocked"] = "pending"
    reason: Literal["budget_exceeded", "authority_missing", "observations_missing"] | None = None
    substitutions: list[AgentCountEvidenceSubstitutionTrace | AgentNutritionEvidenceSubstitutionTrace] = Field(default_factory=list, max_length=14)
    optional_tools: list[str] = Field(default_factory=list, max_length=14)
    scope_quotes: list[str] = Field(default_factory=list, max_length=8)


class AgentRequestContextTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: Literal["Asia/Shanghai"] = "Asia/Shanghai"


class AgentNutritionItemFact(BaseModel):
    model_config = ConfigDict(extra='forbid')
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    meal_type: Literal['早餐','午餐','晚餐','加餐']
    meal_id: str
    item_id: str
    food_name: str
    amount_g: float = Field(gt=0,allow_inf_nan=False)
    protein_g: float = Field(ge=0,allow_inf_nan=False)


class AgentNutritionActionTrace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['add_one_vegetable']
    scope: Literal['next_matching_meal']
    precondition: Literal['fits_diet_restrictions_and_previously_tolerated']


class AgentNutritionDeliveryTrace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    mode: Literal['food_protein_details'] = 'food_protein_details'
    source_call_ids: list[str] = Field(min_length=1,max_length=3)
    source_fingerprints: list[str] = Field(min_length=1,max_length=3)
    requested_dates: list[str] = Field(min_length=1,max_length=31)
    meal_type: Literal['早餐','午餐','晚餐','加餐']
    scope_quote: str
    advice_quotes: list[str] = Field(default_factory=list)
    max_actions: Literal[0,1]
    actions: list[AgentNutritionActionTrace] = Field(default_factory=list,max_length=1)
    item_facts: list[AgentNutritionItemFact] = Field(default_factory=list)
    empty_dates: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_sources_and_actions(self) -> Self:
        from datetime import date
        if len(self.source_call_ids)!=len(self.source_fingerprints) or len(set(self.source_call_ids))!=len(self.source_call_ids):
            raise ValueError('source IDs and fingerprints must pair uniquely')
        if len(self.actions)!=self.max_actions:
            raise ValueError('all promised bounded actions must be delivered exactly once')
        if self.requested_dates!=sorted(set(self.requested_dates)) or any(date.fromisoformat(d).isoformat()!=d for d in self.requested_dates):
            raise ValueError('canonical date scope required')
        if (len({f.item_id for f in self.item_facts})!=len(self.item_facts)
            or any(f.date not in self.requested_dates or f.meal_type!=self.meal_type for f in self.item_facts)
            or not set(self.empty_dates)<=set(self.requested_dates)
            or set(self.empty_dates)&{f.date for f in self.item_facts}):
            raise ValueError('food facts and missing dates must match the actual selected scope')
        return self


class AgentTrainingDeliveryTrace(BaseModel):
    """Checked delivery receipt; no raw health/profile strings are copied here."""
    model_config = ConfigDict(extra="forbid")
    version: Literal[1, 2] = 1
    mode: Literal['training_counters', 'temporary_training']
    source_call_ids: list[str] = Field(min_length=1, max_length=10)
    source_fingerprints: list[str] = Field(min_length=1, max_length=10)
    history_session_count: int | None = Field(default=None, ge=0)
    plan_completed_days: int | None = Field(default=None, ge=0, le=7)
    baseline_weekly_sets: int | None = Field(default=None, ge=1)
    temporary_weekly_sets: int | None = Field(default=None, ge=0)
    temporary_days: int | None = Field(default=None, ge=0, le=7)
    duration_days: int | None = Field(default=None, ge=1, le=14)
    session_cap_minutes: int | None = Field(default=None, ge=5, le=60)
    session_cap_source: Literal['user', 'unspecified'] | None = None
    estimated_session_seconds: list[int] | None = Field(default=None, max_length=7)
    allocation_status: Literal['conditional_reduced_schedule', 'no_work_sets_fit'] | None = None

    @model_validator(mode='after')
    def validate_delivery_arithmetic(self) -> Self:
        if (len(self.source_call_ids) != len(self.source_fingerprints)
                or len(set(self.source_call_ids)) != len(self.source_call_ids)):
            raise ValueError('delivery sources must be unique and paired')
        if self.mode == 'temporary_training':
            if any(v is None for v in (self.baseline_weekly_sets, self.temporary_weekly_sets,
                                       self.temporary_days, self.duration_days)):
                raise ValueError('temporary delivery requires all adjustment bounds')
            if self.temporary_weekly_sets >= self.baseline_weekly_sets:
                raise ValueError('temporary delivery must reduce weekly work sets')
            if self.version == 1:
                if not self.session_cap_minutes or not self.temporary_days or not self.temporary_weekly_sets:
                    raise ValueError('v1 requires positive workload and explicit time bounds')
            else:
                if self.session_cap_source not in {'user', 'unspecified'}:
                    raise ValueError('v2 requires time-limit provenance')
                if (self.session_cap_minutes is None) != (self.session_cap_source == 'unspecified'):
                    raise ValueError('time limit must agree with user provenance')
                if not isinstance(self.estimated_session_seconds, list) or len(self.estimated_session_seconds) != self.temporary_days:
                    raise ValueError('timing must cover each temporary session')
                if any(type(s) is not int or s < 240 or self.session_cap_minutes is not None and s > self.session_cap_minutes * 60 for s in self.estimated_session_seconds):
                    raise ValueError('session estimate must fit its complete time allocation')
                empty = self.temporary_weekly_sets == 0
                if empty != (self.temporary_days == 0) or empty != (self.allocation_status == 'no_work_sets_fit'):
                    raise ValueError('zero work sets, zero sessions and allocation status must agree')
                if not empty and self.allocation_status != 'conditional_reduced_schedule':
                    raise ValueError('nonempty allocation requires reduced-schedule status')
        elif self.history_session_count is None or self.plan_completed_days is None:
            raise ValueError('counter delivery requires both distinct counters')
        return self


class AgentAssessmentFinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: Literal['nutrition_personal_adequacy', 'exercise_load_exemption', 'training_effect_guarantee', 'clinical_exclusion', 'training_action_conflict', 'training_trigger_unresolved', 'training_variant_unsupported', 'symptom_clarification_redundant', 'training_completion_scope', 'daily_function_scope', 'clinical_care_omission']
    kind: Literal['assertion','omission'] = 'assertion'
    quote: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)


class AgentAssessmentExcludedSpan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    codes: list[str] = Field(min_length=1)


class AgentAssessmentRepairRequirement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: Literal['nutrition_personal_adequacy', 'exercise_load_exemption', 'training_effect_guarantee', 'clinical_exclusion', 'training_action_conflict', 'training_trigger_unresolved', 'training_variant_unsupported', 'symptom_clarification_redundant', 'training_completion_scope', 'daily_function_scope', 'clinical_care_omission']
    instruction: str


class AgentAssessmentRepairInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    working_draft: str
    excluded_spans: list[AgentAssessmentExcludedSpan] = Field(min_length=1)
    requirements: list[AgentAssessmentRepairRequirement] = Field(min_length=1)


class AgentAssessmentDeliveryTrace(BaseModel):
    """A scoped rule/repair audit, not a correctness or medical approval score."""
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    status: Literal['pending', 'corrected', 'blocked', 'interrupted']
    checks: Literal['assertion_patterns_v1', 'assertion_patterns_v2', 'assertion_scope_v3', 'assertion_scope_v4', 'assertion_scope_v5', 'assertion_scope_v6', 'assertion_scope_v7', 'assertion_scope_v8', 'assertion_scope_v9', 'assertion_scope_v10', 'assertion_scope_v11', 'assertion_scope_v12', 'assertion_scope_v13']
    source_call_ids: list[str]
    draft: str
    draft_sha256: str
    findings: list[AgentAssessmentFinding]
    current_care_basis: list[dict[str, Any]] = Field(default_factory=list)
    current_care_fallback_preserved: bool = False
    remaining_findings: list[AgentAssessmentFinding]
    repair_attempts: int = Field(ge=0, le=1)
    repair_draft: str | None = None
    repair_input: AgentAssessmentRepairInput | None = None
    repair_input_sha256: str | None = None

    @model_validator(mode='after')
    def validate_original_evidence(self) -> Self:
        import hashlib
        if hashlib.sha256(self.draft.encode()).hexdigest() != self.draft_sha256:
            raise ValueError('assessment draft fingerprint mismatch')
        if not self.findings or any(self.draft[f.start:f.end] != f.quote for f in self.findings):
            raise ValueError('assessment findings must quote exact draft spans')
        if self.status == 'corrected' and (self.repair_attempts != 1 or self.remaining_findings):
            raise ValueError('corrected means one bounded repair with no remaining rule matches')
        if self.repair_input is not None:
            import json
            value=self.repair_input.model_dump()
            digest=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if digest != self.repair_input_sha256:
                raise ValueError('assessment repair input fingerprint mismatch')
            spans=self.repair_input.excluded_spans
            if any(not 0<=s.start<s.end<=len(self.draft) for s in spans) or any(a.end>b.start for a,b in zip(spans,spans[1:])):
                raise ValueError('assessment excluded spans must be ordered and within original draft')
            if any(not any(s.start<=f.start<f.end<=s.end and f.code in s.codes for s in spans) for f in self.findings):
                raise ValueError('assessment repair input must account for every finding')
        elif self.repair_input_sha256 is not None or (self.checks in {'assertion_scope_v4','assertion_scope_v5','assertion_scope_v6', 'assertion_scope_v7', 'assertion_scope_v8', 'assertion_scope_v9', 'assertion_scope_v10', 'assertion_scope_v11', 'assertion_scope_v12', 'assertion_scope_v13'} and self.repair_attempts):
            raise ValueError('assessment v4 attempted repair requires its recorded input')
        return self


class AgentNutritionResponseGoal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Literal['food_action','outing_action']
    source_quote: str = Field(min_length=1)
    instruction: str


class AgentNutritionGoalContract(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    goals: list[AgentNutritionResponseGoal] = Field(max_length=2)
    recent_scope_default: bool
    advice_limit: dict[str, Any]
    source_query: str = Field(min_length=1,max_length=8000)
    limitations: str

    @model_validator(mode='after')
    def check_goals(self) -> Self:
        if len({g.id for g in self.goals})!=len(self.goals) or any(g.source_quote not in self.source_query for g in self.goals):
            raise ValueError('nutrition goals require distinct IDs and exact request quotes')
        return self


class AgentNutritionGoalFinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: Literal['diet_action_missing','outing_action_missing','outing_scope_contracted',
                  'unsupported_relative_restriction','record_meal_count_mismatch','intake_completeness_not_established']
    kind: Literal['omission','assertion']
    goal_id: Literal['food_action','outing_action'] | None = None
    quote: str | None = None
    start: int | None = Field(default=None,ge=0)
    end: int | None = Field(default=None,gt=0)
    evidence: dict[str, Any] | None = None


class AgentNutritionGoalDeliveryTrace(BaseModel):
    """Bounded goal/check receipt; corrected does not mean business success."""
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    checks: Literal['nutrition_goals_v1']
    status: Literal['pending','corrected','blocked','interrupted']
    contract: AgentNutritionGoalContract
    draft: str
    draft_sha256: str
    findings: list[AgentNutritionGoalFinding] = Field(min_length=1)
    remaining_findings: list[AgentNutritionGoalFinding]
    repair_attempts: int = Field(ge=0,le=1)
    repair_draft: str | None = None
    final_draft: str | None = None
    final_findings: list[AgentNutritionGoalFinding] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_receipt(self) -> Self:
        import hashlib
        if hashlib.sha256(self.draft.encode()).hexdigest()!=self.draft_sha256:
            raise ValueError('nutrition draft fingerprint mismatch')
        goals={g.id for g in self.contract.goals}
        for text,findings in [(self.draft,self.findings),(self.repair_draft or self.draft,self.remaining_findings),(self.final_draft or '',self.final_findings)]:
            for f in findings:
                if f.kind=='omission':
                    if f.goal_id not in goals or any(v is not None for v in (f.start,f.end,f.quote)):
                        raise ValueError('omissions refer to a requested goal, never an invented quote')
                elif f.start is None or f.end is None or not 0<=f.start<f.end<=len(text) or text[f.start:f.end]!=f.quote:
                    raise ValueError('nutrition assertions require exact draft spans')
        if self.final_findings and (not self.final_draft or self.status!='blocked'):
            raise ValueError('failed final goal check must retain final draft and block delivery')
        if self.status=='corrected' and (self.repair_attempts!=1 or self.remaining_findings or self.final_findings or not self.repair_draft):
            raise ValueError('corrected requires one recorded repair without remaining matches')
        return self


class AgentToolIdentityChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    generation: int = Field(ge=1)
    response_index: int = Field(ge=0)
    call_index: int = Field(ge=0)
    tool_name: str
    execution_id: str = Field(min_length=1,max_length=120)
    reason: Literal['missing','blank','too_long','duplicate','invalid_characters']
    provider_id: str | None = Field(default=None,max_length=24578)
    provider_id_encoding: Literal['literal','json_string'] = 'literal'
    provider_id_length: int | None = Field(default=None,ge=0)
    provider_id_sha256: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')
    provider_id_truncated: bool = False
    arguments_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')

    @model_validator(mode='after')
    def validate_provider_label(self) -> Self:
        import hashlib,json
        original=json.loads(self.provider_id) if self.provider_id_encoding=='json_string' and self.provider_id is not None else self.provider_id
        if original is None:
            if self.reason!='missing' or self.provider_id_length is not None or self.provider_id_sha256 is not None or self.provider_id_truncated or self.provider_id_encoding!='literal':
                raise ValueError('missing labels must be explicit')
        elif not isinstance(original,str):raise ValueError('provider label is a string')
        elif self.provider_id_truncated:
            if len(original)!=4096 or self.provider_id_length is None or self.provider_id_length<=4096 or self.provider_id_sha256 is None or self.reason not in {'too_long','invalid_characters'}:
                raise ValueError('truncated labels retain prefix, full length and fingerprint')
        elif self.provider_id_length!=len(original) or self.provider_id_sha256!=hashlib.sha256(original.encode('utf8',errors='surrogatepass')).hexdigest():
            raise ValueError('provider label fingerprint mismatch')
        if original==self.execution_id:raise ValueError('identity repair must change label')
        return self


class AgentToolIdentityTrace(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: Literal[1] = 1
    scope: Literal['read_graph_run'] = 'read_graph_run'
    changes: list[AgentToolIdentityChange] = Field(min_length=1)

    @model_validator(mode='after')
    def unique_execution_ids(self) -> Self:
        ids=[c.execution_id for c in self.changes]
        if len(ids)!=len(set(ids)):raise ValueError('repaired execution IDs must be unique')
        return self


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
    training_delivery: AgentTrainingDeliveryTrace | None = None
    nutrition_delivery: AgentNutritionDeliveryTrace | None = None
    assessment_delivery: AgentAssessmentDeliveryTrace | None = None
    nutrition_goal_delivery: AgentNutritionGoalDeliveryTrace | None = None
    tool_call_identity: AgentToolIdentityTrace | None = None
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
        if self.assessment_delivery is None:
            serialized.pop('assessment_delivery', None)
        if self.nutrition_goal_delivery is None:
            serialized.pop('nutrition_goal_delivery', None)
        if self.tool_call_identity is None:
            serialized.pop('tool_call_identity', None)
        if self.training_delivery is None:
            serialized.pop("training_delivery", None)
        if self.nutrition_delivery is None:
            serialized.pop("nutrition_delivery", None)
        if self.task_state is None:
            serialized.pop("task_state", None)
        return serialized
