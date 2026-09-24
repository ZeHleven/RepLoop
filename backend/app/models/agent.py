import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), index=True
    )
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    pending_clarification: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_agent_run_user_idempotency",
        ),
        Index(
            "uq_agent_runs_one_running_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        CheckConstraint(
            "execution_mode IS NULL OR execution_mode IN "
            "('direct', 'planned', 'clarify', 'safe_stop')",
            name="ck_agent_runs_execution_mode",
        ),
        CheckConstraint(
            "intent_domain IN ('general', 'profile', 'health', "
            "'workout_plan', 'workout_session', 'workout_history', "
            "'workout_progress', 'nutrition')",
            name="ck_agent_runs_intent_domain",
        ),
        CheckConstraint(
            "request_kind IN ('query', 'assessment', 'generation', "
            "'mutation', 'proposal_decision')",
            name="ck_agent_runs_request_kind",
        ),
        CheckConstraint(
            "requested_effect IN ('read', 'create', 'update', 'delete', "
            "'decide')",
            name="ck_agent_runs_requested_effect",
        ),
        CheckConstraint(
            "jsonb_typeof(change_requests) = 'array'",
            name="ck_agent_runs_change_requests_array",
        ),
        CheckConstraint(
            "jsonb_typeof(evidence_requirements) = 'array'",
            name="ck_agent_runs_evidence_requirements_array",
        ),
        CheckConstraint(
            "requested_output IN ('answer', 'daily_meal_plan')",
            name="ck_agent_runs_requested_output",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(20), default="running", server_default="running", index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    primary_intent: Mapped[str | None] = mapped_column(String(60), nullable=True)
    intent_domain: Mapped[str] = mapped_column(
        String(40), default="general", server_default="general"
    )
    request_kind: Mapped[str] = mapped_column(
        String(30), default="query", server_default="query"
    )
    requested_effect: Mapped[str] = mapped_column(
        String(20), default="read", server_default="read"
    )
    change_requests: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    evidence_requirements: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    requested_output: Mapped[str] = mapped_column(
        String(40), default="answer", server_default="answer"
    )
    resolved_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    references: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    expanded_intents: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    subtasks: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    missing_slots: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    tool_allowlist: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    execution_mode: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    execution_trace: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )
    risk_level: Mapped[str] = mapped_column(
        String(20), default="low", server_default="low"
    )
    clarification_required: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    clarification_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    understanding_version: Mapped[str | None] = mapped_column(
        String(20), default="v6", server_default="v6", nullable=True
    )
    intent_source: Mapped[str] = mapped_column(
        String(20), default="rules", server_default="rules"
    )
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    intent_fallback_reason: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    intent_error_category: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        Index(
            "uq_agent_messages_one_assistant_per_run",
            "run_id",
            unique=True,
            postgresql_where=text(
                "role = 'assistant' AND run_id IS NOT NULL"
            ),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_conversations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    content_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )


class AgentArtifact(Base):
    __tablename__ = "agent_artifacts"
    __table_args__ = (
        Index(
            "uq_agent_artifacts_one_active_type_per_conversation",
            "conversation_id",
            "artifact_type",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_agent_artifacts_user_conversation_status",
            "user_id",
            "conversation_id",
            "status",
        ),
        CheckConstraint(
            "artifact_type IN ('daily_meal_plan_v1')",
            name="ck_agent_artifacts_type",
        ),
        CheckConstraint(
            "status IN ('active', 'superseded', 'proposed', 'consumed', 'expired')",
            name="ck_agent_artifacts_status",
        ),
        CheckConstraint(
            "jsonb_typeof(payload_data) = 'object'",
            name="ck_agent_artifacts_payload_object",
        ),
        CheckConstraint(
            "jsonb_typeof(context_fingerprints) = 'object'",
            name="ck_agent_artifacts_context_fingerprints_object",
        ),
        CheckConstraint(
            "payload_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_agent_artifacts_fingerprint",
        ),
        CheckConstraint(
            "expires_at > created_at AND "
            "expires_at <= created_at + INTERVAL '24 hours'",
            name="ck_agent_artifacts_expiry_window",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        index=True,
    )
    source_run_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    artifact_type: Mapped[str] = mapped_column(String(60), index=True)
    schema_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    payload_data: Mapped[dict] = mapped_column(JSONB)
    payload_fingerprint: Mapped[str] = mapped_column(String(64))
    context_fingerprints: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentToolCall(Base):
    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        Index(
            "uq_agent_tool_calls_run_call_id",
            "run_id",
            "call_id",
            unique=True,
            postgresql_where=text("call_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    call_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(100), index=True)
    arguments_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    result_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), default="completed", server_default="completed"
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AgentProposal(Base):
    __tablename__ = "agent_proposals"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "proposal_type",
            name="uq_agent_proposals_run_type",
        ),
        UniqueConstraint(
            "user_id",
            "decision_client_request_id",
            name="uq_agent_proposals_user_decision_request",
        ),
        UniqueConstraint(
            "user_id",
            "creation_client_request_id",
            name="uq_agent_proposals_user_creation_request",
        ),
        Index(
            "ix_agent_proposals_target",
            "user_id",
            "target_kind",
            "target_id",
            "status",
        ),
        Index(
            "ix_agent_proposals_pending_expiry",
            "expires_at",
            postgresql_where=text("status = 'pending_confirmation'"),
        ),
        CheckConstraint(
            "status IN ('pending_confirmation', 'applied', 'rejected', "
            "'expired', 'stale', 'failed')",
            name="ck_agent_proposals_status",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_agent_proposals_version_positive",
        ),
        CheckConstraint(
            "jsonb_typeof(payload_data) = 'object'",
            name="ck_agent_proposals_payload_object",
        ),
        CheckConstraint(
            "(payload_fingerprint IS NULL OR "
            "payload_fingerprint ~ '^[0-9a-f]{64}$') AND "
            "(base_plan_fingerprint IS NULL OR "
            "base_plan_fingerprint ~ '^[0-9a-f]{64}$') AND "
            "(result_plan_fingerprint IS NULL OR "
            "result_plan_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_agent_proposals_fingerprints",
        ),
        CheckConstraint(
            "expires_at IS NULL OR "
            "(expires_at > created_at AND "
            "expires_at <= created_at + INTERVAL '72 hours')",
            name="ck_agent_proposals_expiry_window",
        ),
        CheckConstraint(
            "(decision_action IS NULL AND "
            "decision_client_request_id IS NULL AND "
            "confirmed_at IS NULL AND rejected_at IS NULL) OR "
            "(decision_action = 'confirm' AND "
            "decision_client_request_id IS NOT NULL AND "
            "confirmed_at IS NOT NULL AND rejected_at IS NULL) OR "
            "(decision_action = 'reject' AND "
            "decision_client_request_id IS NOT NULL AND "
            "confirmed_at IS NULL AND rejected_at IS NOT NULL)",
            name="ck_agent_proposals_decision_fields",
        ),
        CheckConstraint(
            "status <> 'pending_confirmation' OR "
            "(decision_action IS NULL AND applied_at IS NULL AND "
            "result_plan_id IS NULL AND result_plan_fingerprint IS NULL AND "
            "result_data IS NULL AND last_error_code IS NULL)",
            name="ck_agent_proposals_pending_clean",
        ),
        CheckConstraint(
            "(status = 'applied' AND decision_action = 'confirm' AND "
            "applied_at IS NOT NULL AND last_error_code IS NULL AND ("
            "(proposal_type IN ('plan_adjustment_v1', 'plan_adjustment_v2') AND "
            "result_plan_id IS NOT NULL AND result_plan_fingerprint IS NOT NULL) OR "
            "(proposal_type NOT IN ('plan_adjustment_v1', 'plan_adjustment_v2') AND "
            "result_data IS NOT NULL))) OR "
            "(status <> 'applied' AND applied_at IS NULL AND "
            "result_plan_id IS NULL AND result_plan_fingerprint IS NULL AND "
            "result_data IS NULL)",
            name="ck_agent_proposals_applied_result",
        ),
        CheckConstraint(
            "(status = 'rejected' AND decision_action = 'reject') OR "
            "(status <> 'rejected' AND "
            "(decision_action IS NULL OR decision_action <> 'reject'))",
            name="ck_agent_proposals_rejection_state",
        ),
        CheckConstraint(
            "proposal_type NOT IN ('plan_adjustment_v1', 'plan_adjustment_v2', "
            "'plan_deletion_v1') OR ("
            "payload_fingerprint IS NOT NULL AND "
            "base_plan_id IS NOT NULL AND "
            "base_plan_fingerprint IS NOT NULL AND "
            "expires_at IS NOT NULL AND "
            "((proposal_type = 'plan_adjustment_v1' AND "
            "(target_kind IS NULL OR target_kind = 'workout_plan') AND "
            "(target_id IS NULL OR target_id = base_plan_id)) OR "
            "(proposal_type <> 'plan_adjustment_v1' AND "
            "target_kind IS NOT DISTINCT FROM 'workout_plan' AND "
            "target_id IS NOT DISTINCT FROM base_plan_id)) AND "
            "payload_data ->> 'proposal_type' "
            "IS NOT DISTINCT FROM proposal_type AND "
            "payload_data #>> '{target,base_plan_id}' "
            "IS NOT DISTINCT FROM base_plan_id AND "
            "payload_data #>> '{target,base_plan_fingerprint}' "
            "IS NOT DISTINCT FROM base_plan_fingerprint AND ("
            "(proposal_type = 'plan_adjustment_v1' AND "
            "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '1.0.0') OR "
            "(proposal_type = 'plan_adjustment_v2' AND "
            "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '2.0.0') OR "
            "(proposal_type = 'plan_deletion_v1' AND "
            "payload_data ->> 'schema_version' IS NOT DISTINCT FROM '1.0.0')))",
            name="ck_agent_proposals_plan_adjustment_fields",
        ),
        CheckConstraint(
            "origin IN ('agent_chat', 'manual_editor', 'workout_completion') AND "
            "((origin = 'agent_chat' AND conversation_id IS NOT NULL) OR "
            "(origin IN ('manual_editor', 'workout_completion') AND "
            "conversation_id IS NULL AND run_id IS NULL))",
            name="ck_agent_proposals_origin",
        ),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    run_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True
    )
    proposal_type: Mapped[str] = mapped_column(String(60), index=True)
    origin: Mapped[str] = mapped_column(
        String(20), default="agent_chat", server_default="agent_chat"
    )
    creation_client_request_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    target_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    payload_data: Mapped[dict] = mapped_column(JSONB)
    payload_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    base_plan_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    base_plan_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending_confirmation",
        server_default="pending_confirmation",
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_action: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    decision_client_request_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_plan_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    result_plan_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    result_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        UniqueConstraint("user_id", "memory_key", name="uq_agent_memory_user_key"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    memory_key: Mapped[str] = mapped_column(String(120))
    value_data: Mapped[dict] = mapped_column(JSONB)
    source_message_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("agent_messages.id", ondelete="SET NULL"), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
