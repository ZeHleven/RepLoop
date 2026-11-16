from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanExerciseCandidate(_StrictModel):
    item_key: str = Field(
        min_length=5,
        max_length=160,
        pattern=r"^(planned|new):[A-Za-z0-9._:-]+$",
    )
    exercise_id: str = Field(min_length=1, max_length=100)
    day_of_week: int = Field(ge=1, le=7)
    sets: int = Field(ge=1, le=8)
    reps: str = Field(min_length=1, max_length=20)
    rest_seconds: int = Field(ge=15, le=600)
    recommended_weight_kg: float | None = Field(default=None, ge=0, le=1000)
    order_index: int = Field(ge=0, le=49)

    @field_validator("reps")
    @classmethod
    def normalize_reps(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reps cannot be blank")
        return normalized


class PlanCandidate(_StrictModel):
    duration_weeks: int = Field(ge=2, le=12)
    training_days: list[int] = Field(min_length=1, max_length=7)
    exercises: list[PlanExerciseCandidate] = Field(min_length=1, max_length=50)

    @field_validator("training_days")
    @classmethod
    def validate_training_days(cls, values: list[int]) -> list[int]:
        if any(value < 1 or value > 7 for value in values):
            raise ValueError("training days must be between 1 and 7")
        if len(values) != len(set(values)):
            raise ValueError("training days must be unique")
        return sorted(values)


class CreatePlanAdjustmentProposalRequest(_StrictModel):
    client_request_id: str = Field(min_length=8, max_length=120)
    expected_base_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate: PlanCandidate


class CreatePlanDeletionProposalRequest(_StrictModel):
    client_request_id: str = Field(min_length=8, max_length=120)
    expected_base_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlanExerciseSnapshotV2(PlanExerciseCandidate):
    exercise_name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=30)


class PlanSnapshotV2(_StrictModel):
    name: str = Field(min_length=1, max_length=100)
    goal: str | None = Field(default=None, max_length=50)
    duration_weeks: int = Field(ge=2, le=12)
    days_per_week: int = Field(ge=1, le=7)
    training_days: list[int] = Field(min_length=1, max_length=7)
    exercises: list[PlanExerciseSnapshotV2] = Field(min_length=1, max_length=50)


class PlanProposalTarget(_StrictModel):
    resource_type: Literal["workout_plan"] = "workout_plan"
    base_plan_id: str = Field(min_length=1, max_length=100)
    base_plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    health_context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


PlanChangeTypeV2 = Literal[
    "update_schedule",
    "add_exercise",
    "remove_exercise",
    "replace_exercise",
    "move_exercise",
    "adjust_exercise_target",
]


class PlanChangeV2(_StrictModel):
    change_type: PlanChangeTypeV2
    stable_display_key: str = Field(min_length=1, max_length=160)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=300)
    safety_priority: bool = False


class PlanAdjustmentPayloadV2(_StrictModel):
    schema_version: Literal["2.0.0"] = "2.0.0"
    proposal_type: Literal["plan_adjustment_v2"] = "plan_adjustment_v2"
    target: PlanProposalTarget
    before: PlanSnapshotV2
    after: PlanSnapshotV2
    changes: list[PlanChangeV2] = Field(min_length=1, max_length=160)
    rationale: list[str] = Field(default_factory=list, max_length=12)
    safety_notes: list[str] = Field(default_factory=list, max_length=12)


class PlanDeletionPayloadV1(_StrictModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    proposal_type: Literal["plan_deletion_v1"] = "plan_deletion_v1"
    target: PlanProposalTarget
    before: PlanSnapshotV2
    consequences: list[str] = Field(min_length=1, max_length=12)
    safety_notes: list[str] = Field(default_factory=list, max_length=12)


class PlanEditConstraints(_StrictModel):
    duration_weeks_min: int = 2
    duration_weeks_max: int = 12
    sets_min: int = 1
    sets_max: int = 8
    rest_seconds_min: int = 15
    rest_seconds_max: int = 600
    recommended_weight_kg_max: float = 1000
    total_exercises_max: int = 50


class PlanEditContext(_StrictModel):
    base_plan: PlanSnapshotV2
    base_plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    health_context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    exercise_options: list[dict[str, Any]] = Field(default_factory=list)
    exercise_notices: dict[str, str] = Field(default_factory=dict)
    constraints: PlanEditConstraints = Field(default_factory=PlanEditConstraints)
    active_session: bool = False
    proposals_enabled: bool


class GenericProposalDecisionRequest(_StrictModel):
    expected_version: int = Field(ge=1)
    client_request_id: str = Field(min_length=8, max_length=120)


class GenericProposalReadResponse(_StrictModel):
    id: str
    proposal_type: str
    origin: Literal["agent_chat", "manual_editor", "workout_completion"]
    status: Literal[
        "pending_confirmation", "applied", "rejected", "expired", "stale", "failed"
    ]
    version: int = Field(ge=1)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload: dict[str, Any]
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    allowed_actions: list[Literal["confirm", "reject"]] = Field(default_factory=list)
    result: dict[str, Any] | None = None


class GenericProposalDecisionResponse(_StrictModel):
    id: str
    proposal_type: str
    status: Literal["applied", "rejected"]
    version: int = Field(ge=2)
    applied: bool
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_plan_id: str | None = None
    result_plan_fingerprint: str | None = None
    result_data: dict[str, Any] | None = None
    decided_at: datetime


class PlanProposalReference(_StrictModel):
    id: str
    proposal_type: str
    status: str
    version: int = Field(ge=1)
    expires_at: datetime
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_time(self):
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include timezone")
        return self
