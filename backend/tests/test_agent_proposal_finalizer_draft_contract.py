from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalDraft,
)
from app.schemas.agent_planning import ProposalFinalizationDecision


def _valid_draft() -> dict:
    return {
        "proposal_type": "plan_adjustment_v1",
        "changes": [{
            "change_type": "adjust_exercise_target",
            "stable_display_key": "day-1-order-0",
            "before": {"sets": 4},
            "after": {"sets": 3},
            "reason": "近期完成率偏低，先保守降低单次训练量。",
            "safety_priority": False,
        }],
        "rationale": ["降低执行门槛，提高连续完成概率。"],
        "safety_notes": [],
        "requested_ttl_hours": 24,
    }


def test_adjustment_outcome_parses_a_typed_proposal_draft():
    decision = ProposalFinalizationDecision(
        outcome="adjustment_proposal",
        reply="建议减少一组；这是待确认提案，尚未执行。",
        proposal_draft=_valid_draft(),
    )

    assert isinstance(
        decision.proposal_draft,
        PlanAdjustmentProposalDraft,
    )
    assert decision.proposal_draft.model_dump(
        mode="json",
        exclude_unset=True,
    ) == _valid_draft()


@pytest.mark.parametrize(
    "invalid_case",
    (
        "missing_required_change_field",
        "extra_draft_field",
        "coerced_ttl",
        "mismatched_diff_fields",
    ),
)
def test_proposal_draft_rejects_invalid_nested_shapes(invalid_case: str):
    draft = deepcopy(_valid_draft())
    if invalid_case == "missing_required_change_field":
        del draft["changes"][0]["safety_priority"]
    elif invalid_case == "extra_draft_field":
        draft["server_owned_payload"] = {"must": "not be accepted"}
    elif invalid_case == "coerced_ttl":
        draft["requested_ttl_hours"] = "24"
    else:
        draft["changes"][0]["after"] = {"reps": 8}

    with pytest.raises(ValidationError) as captured:
        ProposalFinalizationDecision(
            outcome="adjustment_proposal",
            reply="这是待确认提案，尚未执行。",
            proposal_draft=draft,
        )

    assert any(
        "proposal_draft" in error["loc"]
        for error in captured.value.errors()
    )


def test_adjustment_outcome_requires_a_proposal_draft():
    with pytest.raises(
        ValidationError,
        match="adjustment_proposal requires proposal_draft",
    ):
        ProposalFinalizationDecision(
            outcome="adjustment_proposal",
            reply="这是待确认提案，尚未执行。",
            proposal_draft=None,
        )


@pytest.mark.parametrize(
    "outcome",
    (
        "informational_answer",
        "no_change_needed",
        "insufficient_evidence",
    ),
)
def test_non_proposal_outcome_rejects_a_proposal_draft(outcome: str):
    with pytest.raises(
        ValidationError,
        match="only adjustment_proposal may include proposal_draft",
    ):
        ProposalFinalizationDecision(
            outcome=outcome,
            reply="本轮不形成调整提案。",
            proposal_draft=_valid_draft(),
        )


@pytest.mark.parametrize(
    "outcome",
    (
        "informational_answer",
        "no_change_needed",
        "insufficient_evidence",
    ),
)
def test_non_proposal_outcome_accepts_only_a_null_draft(outcome: str):
    decision = ProposalFinalizationDecision(
        outcome=outcome,
        reply="本轮不形成调整提案。",
        proposal_draft=None,
    )

    assert decision.proposal_draft is None
