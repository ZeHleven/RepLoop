"""Privacy-safe trace projection for optional Proposal creation."""

from __future__ import annotations

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalCreationDecision,
)
from app.schemas.agent_trace import (
    AgentExecutionTrace,
    AgentProposalCreationTrace,
    ProposalPersistenceReasonTrace,
)


def attach_proposal_creation_decision(
    trace: AgentExecutionTrace,
    decision: PlanAdjustmentProposalCreationDecision,
) -> AgentExecutionTrace:
    diagnostic = AgentProposalCreationTrace(
        eligible=decision.eligible,
        reason_code=decision.reason_code,
    )
    return trace.model_copy(update={
        "trace_version": "1.2",
        "proposal_creation": diagnostic,
    })


def _eligible_diagnostic(
    trace: AgentExecutionTrace,
) -> AgentProposalCreationTrace:
    diagnostic = trace.proposal_creation
    if diagnostic is None or not diagnostic.eligible:
        raise ValueError("proposal persistence requires an eligible diagnostic")
    return diagnostic


def mark_proposal_persisted(
    trace: AgentExecutionTrace,
    *,
    created: bool,
) -> AgentExecutionTrace:
    diagnostic = _eligible_diagnostic(trace).model_copy(update={
        "persisted": True,
        "persistence_status": "created" if created else "replayed",
        "persistence_reason_code": None,
    })
    return trace.model_copy(update={"proposal_creation": diagnostic})


def mark_proposal_persistence_rejected(
    trace: AgentExecutionTrace,
    *,
    reason_code: ProposalPersistenceReasonTrace,
) -> AgentExecutionTrace:
    diagnostic = _eligible_diagnostic(trace).model_copy(update={
        "persisted": False,
        "persistence_status": "rejected",
        "persistence_reason_code": reason_code,
    })
    return trace.model_copy(update={"proposal_creation": diagnostic})


def mark_proposal_persistence_failed(
    trace: AgentExecutionTrace,
) -> AgentExecutionTrace:
    diagnostic = _eligible_diagnostic(trace).model_copy(update={
        "persisted": False,
        "persistence_status": "failed",
        "persistence_reason_code": None,
    })
    return trace.model_copy(update={"proposal_creation": diagnostic})
