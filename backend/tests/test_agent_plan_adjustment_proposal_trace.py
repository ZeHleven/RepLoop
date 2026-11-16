from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalCreationDecision,
)
from app.schemas.agent_trace import (
    AgentExecutionTrace,
    AgentPlanTrace,
    AgentProposalCreationTrace,
)
from app.services.agent_plan_adjustment_proposal_trace import (
    attach_proposal_creation_decision,
    mark_proposal_persistence_failed,
    mark_proposal_persistence_rejected,
    mark_proposal_persisted,
)
from app.services.agent_tool_registry_shadow_trace import (
    ToolRegistryShadowSession,
    attach_registry_shadow_report,
)


def _trace() -> AgentExecutionTrace:
    return AgentExecutionTrace(
        execution_mode="planned",
        risk_level="low",
        plan=AgentPlanTrace(
            goal="判断训练计划是否需要调整",
            planner_source="planning_gate_v1",
        ),
    )


def test_rejected_creation_records_only_stable_reason_code():
    trace = attach_proposal_creation_decision(
        _trace(),
        PlanAdjustmentProposalCreationDecision(
            eligible=False,
            reason_code="proposal_draft_invalid",
        ),
    )

    assert trace.trace_version == "1.2"
    assert trace.proposal_creation == AgentProposalCreationTrace(
        eligible=False,
        reason_code="proposal_draft_invalid",
        persisted=False,
        persistence_status="not_attempted",
    )
    serialized = trace.model_dump(mode="json")
    assert serialized["proposal_creation"] == {
        "eligible": False,
        "reason_code": "proposal_draft_invalid",
        "persisted": False,
        "persistence_status": "not_attempted",
        "persistence_reason_code": None,
    }
    assert set(serialized["proposal_creation"]) == {
        "eligible",
        "reason_code",
        "persisted",
        "persistence_status",
        "persistence_reason_code",
    }


@pytest.mark.parametrize(
    ("created", "expected_status"),
    [(True, "created"), (False, "replayed")],
)
def test_validated_creation_records_created_and_replayed_persistence(
    created: bool,
    expected_status: str,
):
    attached = attach_proposal_creation_decision(
        _trace(),
        PlanAdjustmentProposalCreationDecision(
            eligible=True,
            initial_status="pending_confirmation",
            ttl_hours=24,
        ),
    )

    persisted = mark_proposal_persisted(attached, created=created)

    assert persisted.proposal_creation is not None
    assert persisted.proposal_creation.eligible is True
    assert persisted.proposal_creation.persisted is True
    assert persisted.proposal_creation.persistence_status == expected_status


def test_persistence_failure_is_visible_without_exception_details():
    attached = attach_proposal_creation_decision(
        _trace(),
        PlanAdjustmentProposalCreationDecision(
            eligible=True,
            initial_status="pending_confirmation",
            ttl_hours=24,
        ),
    )

    failed = mark_proposal_persistence_failed(attached)

    assert failed.proposal_creation is not None
    assert failed.proposal_creation.persisted is False
    assert failed.proposal_creation.persistence_status == "failed"
    assert failed.proposal_creation.persistence_reason_code is None
    assert "exception" not in str(failed.model_dump(mode="json"))


def test_persistence_rejection_records_only_stable_reason_code():
    attached = attach_proposal_creation_decision(
        _trace(),
        PlanAdjustmentProposalCreationDecision(
            eligible=True,
            initial_status="pending_confirmation",
            ttl_hours=24,
        ),
    )

    rejected = mark_proposal_persistence_rejected(
        attached,
        reason_code="run_ownership_lost",
    )

    assert rejected.proposal_creation is not None
    assert rejected.proposal_creation.persisted is False
    assert rejected.proposal_creation.persistence_status == "rejected"
    assert (
        rejected.proposal_creation.persistence_reason_code
        == "run_ownership_lost"
    )


def test_registry_shadow_attachment_preserves_trace_version_1_2():
    attached = attach_proposal_creation_decision(
        _trace(),
        PlanAdjustmentProposalCreationDecision(
            eligible=False,
            reason_code="proposal_draft_invalid",
        ),
    )
    report = ToolRegistryShadowSession(sample_bucket=42).build_report()

    shadowed = attach_registry_shadow_report(
        attached,
        report,
        persist_trace=True,
    )

    assert shadowed.trace_version == "1.2"
    assert shadowed.proposal_creation == attached.proposal_creation
    assert shadowed.tool_registry_shadow == report


def test_proposal_diagnostic_requires_trace_version_1_2():
    with pytest.raises(
        ValidationError,
        match="proposal creation diagnostics require trace version 1.2",
    ):
        AgentExecutionTrace.model_validate({
            **_trace().model_dump(mode="json"),
            "proposal_creation": {
                "eligible": False,
                "reason_code": "proposal_draft_invalid",
                "persisted": False,
                "persistence_status": "not_attempted",
            },
        })
