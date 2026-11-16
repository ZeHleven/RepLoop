from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from app.schemas.agent_trace import AgentExecutionTrace, ExecutionMode


class AgentArtifactActionRequest(BaseModel):
    """A user action emitted by an Artifact card, not inferred from its label."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["save_as_proposal"]
    artifact_id: str = Field(min_length=1, max_length=100)
    expected_version: int = Field(ge=1)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentClarificationActionRequest(BaseModel):
    """A server-issued choice for an active structured clarification."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["select_plan_occurrences"]
    origin_run_id: str = Field(min_length=1, max_length=100)
    choice_ids: list[str] = Field(min_length=1, max_length=7)


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)
    artifact_action: AgentArtifactActionRequest | None = None
    clarification_action: AgentClarificationActionRequest | None = None

    @model_validator(mode="after")
    def actions_are_mutually_exclusive(self):
        if self.artifact_action is not None and self.clarification_action is not None:
            raise ValueError("artifact_action and clarification_action are mutually exclusive")
        return self


class AgentRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)
    client_request_id: str = Field(min_length=8, max_length=120)
    artifact_action: AgentArtifactActionRequest | None = None
    clarification_action: AgentClarificationActionRequest | None = None

    @model_validator(mode="after")
    def actions_are_mutually_exclusive(self):
        if self.artifact_action is not None and self.clarification_action is not None:
            raise ValueError("artifact_action and clarification_action are mutually exclusive")
        return self


class AgentRunCreateResponse(BaseModel):
    run_id: str
    conversation_id: str
    status: Literal["queued", "running", "completed", "failed"]
    poll_after_ms: int = 800


class AgentCard(BaseModel):
    type: str = Field(min_length=1, max_length=60)
    data: dict[str, Any] = Field(default_factory=dict)


class AgentProposalReference(BaseModel):
    id: str
    proposal_type: Literal[
        "plan_adjustment_v1",
        "plan_creation_v1",
        "plan_adjustment_v2",
        "plan_deletion_v1",
        "profile_update_v1",
        "weight_log_create_v1",
        "meal_log_create_v1",
        "daily_meal_log_create_v1",
        "meal_log_delete_v1",
    ]
    status: Literal["pending_confirmation"]
    version: int = Field(ge=1)
    expires_at: datetime
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentArtifactReference(BaseModel):
    id: str
    artifact_type: Literal["daily_meal_plan_v1"]
    status: Literal["active", "superseded", "proposed", "consumed", "expired"]
    version: int = Field(ge=1)
    expires_at: datetime
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class AgentChatResponse(BaseModel):
    reply: str
    conversation_id: str
    run_id: str
    cards: list[AgentCard] = Field(default_factory=list)
    proposal: AgentProposalReference | None = None
    artifact: AgentArtifactReference | None = None

    @model_serializer(mode="wrap")
    def omit_absent_optional_proposal(self, handler):
        data = handler(self)
        if self.proposal is None:
            data.pop("proposal", None)
        if self.artifact is None:
            data.pop("artifact", None)
        return data


class AgentConversationResponse(BaseModel):
    id: str
    title: str | None
    summary: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class AgentMessageResponse(BaseModel):
    id: str
    run_id: str | None
    role: Literal["user", "assistant", "tool", "system"]
    content: str
    content_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    model_config = {"from_attributes": True}


class AgentRunResponse(BaseModel):
    id: str
    status: str
    primary_intent: str | None
    intent_domain: Literal[
        "general",
        "profile",
        "health",
        "workout_plan",
        "workout_session",
        "workout_history",
        "workout_progress",
        "nutrition",
    ]
    request_kind: Literal[
        "query",
        "assessment",
        "generation",
        "mutation",
        "proposal_decision",
    ]
    requested_effect: Literal["read", "create", "update", "delete", "decide"]
    change_requests: list[dict[str, Any]] = Field(default_factory=list)
    evidence_requirements: list[str] = Field(default_factory=list)
    requested_output: Literal["answer", "daily_meal_plan"] = "answer"
    resolved_query: str | None
    references: list[dict[str, Any]] = Field(default_factory=list)
    expanded_intents: list[str] = Field(default_factory=list)
    subtasks: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    tool_allowlist: list[str] = Field(default_factory=list)
    execution_mode: ExecutionMode | None
    execution_trace: AgentExecutionTrace | None
    risk_level: str
    clarification_required: bool
    clarification_question: str | None
    understanding_version: str | None
    intent_source: str
    intent_confidence: float | None
    intent_attempt_count: int
    intent_fallback_reason: str | None
    intent_error_category: str | None
    duration_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    error_code: str | None
    error_message: str | None = None
    reply: str | None = None
    cards: list[AgentCard] = Field(default_factory=list)
    proposal: AgentProposalReference | None = None
    artifact: AgentArtifactReference | None = None
    poll_after_ms: int | None = None
    queued_at: datetime
    processing_started_at: datetime | None
    attempt_count: int
    started_at: datetime
    completed_at: datetime | None
    model_config = {"from_attributes": True}

    @model_serializer(mode="wrap")
    def omit_absent_optional_proposal(self, handler):
        data = handler(self)
        if self.proposal is None:
            data.pop("proposal", None)
        if self.artifact is None:
            data.pop("artifact", None)
        return data
