from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    model_validator,
)

from app.schemas.agent_plan_adjustment_proposal import (
    PlanAdjustmentProposalPayload,
)


PlanAdjustmentProposalStatus = Literal[
    "pending_confirmation",
    "applied",
    "rejected",
    "expired",
    "stale",
    "failed",
]
PlanAdjustmentProposalDecisionAction = Literal["confirm", "reject"]
PlanAdjustmentProposalAllowedAction = Literal["confirm", "reject"]
PlanAdjustmentProposalBusinessErrorCode = Literal[
    "proposal_not_found",
    "proposal_not_pending",
    "proposal_version_conflict",
    "proposal_expired",
    "proposal_feature_disabled",
    "proposal_idempotency_conflict",
    "proposal_base_plan_changed",
    "proposal_health_context_changed",
    "proposal_payload_invalid",
    "proposal_candidate_unavailable",
    "proposal_execution_conflict",
    "proposal_execution_failed",
]


class _StrictFrozenApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanAdjustmentProposalDecisionRequest(_StrictFrozenApiModel):
    expected_version: Annotated[StrictInt, Field(ge=1)]
    client_request_id: Annotated[
        StrictStr,
        Field(min_length=8, max_length=120),
    ]


class PlanAdjustmentProposalAppliedResult(_StrictFrozenApiModel):
    plan_id: str = Field(min_length=1, max_length=100)
    plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    applied_at: datetime

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        if self.applied_at.tzinfo is None:
            raise ValueError("applied_at must include a timezone")
        return self


class PlanAdjustmentProposalReadResponse(_StrictFrozenApiModel):
    id: str = Field(min_length=1, max_length=100)
    proposal_type: Literal["plan_adjustment_v1"]
    status: PlanAdjustmentProposalStatus
    version: int = Field(ge=1)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: PlanAdjustmentProposalPayload
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    allowed_actions: tuple[
        PlanAdjustmentProposalAllowedAction,
        ...,
    ] = ()
    result: PlanAdjustmentProposalAppliedResult | None = None

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        timestamps = (self.expires_at, self.created_at, self.updated_at)
        if any(value.tzinfo is None for value in timestamps):
            raise ValueError("proposal timestamps must include timezones")
        expected_actions = (
            ("confirm", "reject")
            if self.status == "pending_confirmation"
            else ()
        )
        if self.allowed_actions != expected_actions:
            raise ValueError("allowed_actions must match effective status")
        if (self.status == "applied") != (self.result is not None):
            raise ValueError("only applied proposals expose a result")
        return self


class PlanAdjustmentProposalDecisionResponse(_StrictFrozenApiModel):
    id: str = Field(min_length=1, max_length=100)
    proposal_type: Literal["plan_adjustment_v1"]
    status: Literal["applied", "rejected"]
    version: int = Field(ge=2)
    applied: bool
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_plan_id: str | None = Field(default=None, max_length=100)
    result_plan_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    decided_at: datetime

    @model_validator(mode="after")
    def validate_decision_result(self) -> Self:
        if self.decided_at.tzinfo is None:
            raise ValueError("decided_at must include a timezone")
        has_result = (
            self.result_plan_id is not None
            and self.result_plan_fingerprint is not None
        )
        if self.status == "applied":
            if not self.applied or not has_result:
                raise ValueError("applied response requires result plan")
        elif self.applied or has_result:
            raise ValueError("rejected response cannot expose result plan")
        return self


class PlanAdjustmentProposalBusinessError(_StrictFrozenApiModel):
    code: PlanAdjustmentProposalBusinessErrorCode
    message: str = Field(min_length=1, max_length=300)
