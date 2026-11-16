from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)


PlanAdjustmentProposalPayloadErrorCode = Literal[
    "payload_not_object",
    "forbidden_field",
    "missing_base_fingerprint",
    "incomplete_candidate_plan",
    "unsupported_change_type",
    "no_effect_change",
    "invalid_plan_bounds",
    "invalid_target",
]
PlanAdjustmentProposalCreationReasonCode = Literal[
    "feature_disabled",
    "run_ownership_lost",
    "health_red_flag",
    "clarification_required",
    "outcome_not_adjustment_proposal",
    "terminal_action_not_proposal",
    "intent_not_adjustment",
    "plan_evidence_missing",
    "supporting_evidence_missing",
    "deadline_evidence_insufficient",
    "proposal_draft_invalid",
    "proposal_draft_missing",
    "proposal_draft_schema_invalid",
    "proposal_target_mismatch",
    "proposal_frequency_restructure_unsupported",
    "proposal_candidate_build_invalid",
    "proposal_target_ambiguous",
    "proposal_operation_unsupported",
    "proposal_target_value_required",
    "proposal_no_change",
    "proposal_type_not_allowed",
    "proposal_ttl_out_of_range",
]


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanAdjustmentProposalCreationDecision(_StrictFrozenModel):
    eligible: bool
    reason_code: PlanAdjustmentProposalCreationReasonCode | None = None
    initial_status: Literal["pending_confirmation"] | None = None
    ttl_hours: int | None = Field(default=None, ge=1, le=72)

    @model_validator(mode="after")
    def validate_decision_shape(self) -> Self:
        if self.eligible:
            if (
                self.reason_code is not None
                or self.initial_status != "pending_confirmation"
                or self.ttl_hours is None
            ):
                raise ValueError(
                    "eligible proposal decisions require status and TTL"
                )
        elif (
            self.reason_code is None
            or self.initial_status is not None
            or self.ttl_hours is not None
        ):
            raise ValueError(
                "rejected proposal decisions require only a reason code"
            )
        return self


class PlanAdjustmentProposalTarget(_StrictFrozenModel):
    resource_type: Literal["workout_plan"]
    base_plan_id: str = Field(min_length=1, max_length=100)
    base_plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlanAdjustmentExerciseSnapshot(_StrictFrozenModel):
    slot_key: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9_-]*$",
    )
    exercise_id: str = Field(min_length=1, max_length=100)
    exercise_name: str = Field(min_length=1, max_length=100)
    day_of_week: int = Field(ge=1, le=7)
    sets: int = Field(ge=1, le=8)
    reps: str = Field(min_length=1, max_length=20)
    rest_seconds: int = Field(ge=15, le=600)
    recommended_weight_kg: Annotated[float, Field(ge=0, le=1000)] | None
    order_index: int = Field(ge=0, le=20)


class PlanAdjustmentPlanSnapshot(_StrictFrozenModel):
    name: str = Field(min_length=1, max_length=100)
    goal: Annotated[str, Field(min_length=1, max_length=50)] | None
    duration_weeks: int = Field(ge=2, le=12)
    days_per_week: int = Field(ge=1, le=7)
    exercises: tuple[PlanAdjustmentExerciseSnapshot, ...] = Field(
        min_length=1,
        max_length=50,
    )

    @model_validator(mode="after")
    def validate_exercise_slots_and_schedule(self) -> Self:
        slot_keys = [item.slot_key for item in self.exercises]
        if len(slot_keys) != len(set(slot_keys)):
            raise ValueError("invalid_plan_bounds: slot keys must be unique")

        ordered_slots = [
            (item.day_of_week, item.order_index) for item in self.exercises
        ]
        if len(ordered_slots) != len(set(ordered_slots)):
            raise ValueError(
                "invalid_plan_bounds: day and order pairs must be unique"
            )

        scheduled_days = {item.day_of_week for item in self.exercises}
        if self.days_per_week != len(scheduled_days):
            raise ValueError(
                "invalid_plan_bounds: days_per_week must match scheduled days"
            )
        return self


class PlanAdjustmentExerciseTargetValues(_StrictFrozenModel):
    sets: int | None = Field(default=None, ge=1, le=8)
    reps: str | None = Field(default=None, min_length=1, max_length=20)
    rest_seconds: int | None = Field(default=None, ge=15, le=600)
    recommended_weight_kg: float | None = Field(
        default=None,
        ge=0,
        le=1000,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            isinstance(value.get(field_name), bool)
            for field_name in (
                "sets",
                "rest_seconds",
                "recommended_weight_kg",
            )
            if field_name in value
        ):
            raise ValueError("target numeric values cannot be booleans")
        return value

    @model_validator(mode="after")
    def validate_explicit_values(self) -> Self:
        if not self.model_fields_set:
            raise ValueError(
                "incomplete_candidate_plan: target values cannot be empty"
            )
        for field_name in self.model_fields_set - {"recommended_weight_kg"}:
            if getattr(self, field_name) is None:
                raise ValueError(
                    "incomplete_candidate_plan: target values cannot be null"
                )
        return self


class PlanAdjustmentExerciseIdentity(_StrictFrozenModel):
    exercise_id: str = Field(min_length=1, max_length=100)
    exercise_name: str = Field(min_length=1, max_length=100)


class PlanAdjustmentScheduleValues(_StrictFrozenModel):
    duration_weeks: int | None = Field(default=None, ge=2, le=12)
    days_per_week: int | None = Field(default=None, ge=1, le=7)

    @model_validator(mode="before")
    @classmethod
    def reject_boolean_numbers(cls, value: Any) -> Any:
        if isinstance(value, dict) and any(
            isinstance(value.get(field_name), bool)
            for field_name in ("duration_weeks", "days_per_week")
            if field_name in value
        ):
            raise ValueError("schedule numeric values cannot be booleans")
        return value

    @model_validator(mode="after")
    def validate_explicit_values(self) -> Self:
        if not self.model_fields_set:
            raise ValueError(
                "incomplete_candidate_plan: schedule values cannot be empty"
            )
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError(
                "incomplete_candidate_plan: schedule values cannot be null"
            )
        return self


class _PlanAdjustmentChangeBase(_StrictFrozenModel):
    stable_display_key: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)
    safety_priority: bool


class PlanAdjustmentExerciseTargetChange(_PlanAdjustmentChangeBase):
    change_type: Literal["adjust_exercise_target"]
    before: PlanAdjustmentExerciseTargetValues
    after: PlanAdjustmentExerciseTargetValues

    @model_validator(mode="after")
    def validate_effect(self) -> Self:
        if self.before.model_fields_set != self.after.model_fields_set:
            raise ValueError(
                "incomplete_candidate_plan: target diff fields must match"
            )
        if self.before == self.after:
            raise ValueError("no_effect_change: target values are unchanged")
        return self


class PlanAdjustmentExerciseReplacementChange(_PlanAdjustmentChangeBase):
    change_type: Literal["replace_exercise"]
    before: PlanAdjustmentExerciseIdentity
    after: PlanAdjustmentExerciseIdentity

    @model_validator(mode="after")
    def validate_effect(self) -> Self:
        if self.before == self.after:
            raise ValueError("no_effect_change: exercise is unchanged")
        return self


class PlanAdjustmentScheduleChange(_PlanAdjustmentChangeBase):
    change_type: Literal["update_plan_schedule"]
    before: PlanAdjustmentScheduleValues
    after: PlanAdjustmentScheduleValues

    @model_validator(mode="after")
    def validate_effect(self) -> Self:
        if self.before.model_fields_set != self.after.model_fields_set:
            raise ValueError(
                "incomplete_candidate_plan: schedule diff fields must match"
            )
        if self.before == self.after:
            raise ValueError("no_effect_change: schedule values are unchanged")
        return self


PlanAdjustmentChange = Annotated[
    PlanAdjustmentExerciseTargetChange
    | PlanAdjustmentExerciseReplacementChange
    | PlanAdjustmentScheduleChange,
    Field(discriminator="change_type"),
]


class PlanAdjustmentProposalEvidence(_StrictFrozenModel):
    tool_id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$",
    )
    result_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime

    @model_validator(mode="after")
    def validate_observed_at_timezone(self) -> Self:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        return self


PlanAdjustmentExplanation = Annotated[
    str,
    Field(min_length=1, max_length=1000),
]


class PlanAdjustmentProposalDraft(_StrictFrozenModel):
    """Untrusted compact Finalizer draft; the server builds full snapshots."""

    proposal_type: Literal["plan_adjustment_v1"]
    changes: tuple[PlanAdjustmentChange, ...] = Field(
        min_length=1,
        max_length=12,
    )
    rationale: tuple[PlanAdjustmentExplanation, ...] = Field(
        min_length=1,
        max_length=12,
    )
    safety_notes: tuple[PlanAdjustmentExplanation, ...] = Field(
        max_length=12,
    )
    requested_ttl_hours: Annotated[
        StrictInt,
        Field(ge=1, le=72),
    ] | None = None


class PlanAdjustmentProposalPayload(_StrictFrozenModel):
    schema_version: Literal["1.0.0"]
    proposal_type: Literal["plan_adjustment_v1"]
    target: PlanAdjustmentProposalTarget
    before: PlanAdjustmentPlanSnapshot
    after: PlanAdjustmentPlanSnapshot
    changes: tuple[PlanAdjustmentChange, ...] = Field(
        min_length=1,
        max_length=50,
    )
    evidence: tuple[PlanAdjustmentProposalEvidence, ...] = Field(
        min_length=1,
        max_length=12,
    )
    rationale: tuple[PlanAdjustmentExplanation, ...] = Field(
        min_length=1,
        max_length=12,
    )
    safety_notes: tuple[PlanAdjustmentExplanation, ...] = Field(
        max_length=12,
    )

    @model_validator(mode="after")
    def validate_candidate_and_diff_alignment(self) -> Self:
        if self.before == self.after:
            raise ValueError("no_effect_change: candidate plan is unchanged")

        before_by_slot = {
            item.slot_key: item for item in self.before.exercises
        }
        after_by_slot = {item.slot_key: item for item in self.after.exercises}
        for change in self.changes:
            if isinstance(change, PlanAdjustmentScheduleChange):
                self._validate_schedule_change(change)
                continue

            before_exercise = before_by_slot.get(change.stable_display_key)
            after_exercise = after_by_slot.get(change.stable_display_key)
            if before_exercise is None or after_exercise is None:
                raise ValueError(
                    "incomplete_candidate_plan: change slot is not in snapshots"
                )
            if isinstance(change, PlanAdjustmentExerciseTargetChange):
                self._validate_target_change(
                    change,
                    before_exercise,
                    after_exercise,
                )
            else:
                self._validate_replacement_change(
                    change,
                    before_exercise,
                    after_exercise,
                )
        return self

    def _validate_schedule_change(
        self,
        change: PlanAdjustmentScheduleChange,
    ) -> None:
        for field_name in change.before.model_fields_set:
            if getattr(change.before, field_name) != getattr(
                self.before,
                field_name,
            ) or getattr(change.after, field_name) != getattr(
                self.after,
                field_name,
            ):
                raise ValueError(
                    "incomplete_candidate_plan: schedule diff does not match "
                    "snapshots"
                )

    @staticmethod
    def _validate_target_change(
        change: PlanAdjustmentExerciseTargetChange,
        before_exercise: PlanAdjustmentExerciseSnapshot,
        after_exercise: PlanAdjustmentExerciseSnapshot,
    ) -> None:
        for field_name in change.before.model_fields_set:
            if getattr(change.before, field_name) != getattr(
                before_exercise,
                field_name,
            ) or getattr(change.after, field_name) != getattr(
                after_exercise,
                field_name,
            ):
                raise ValueError(
                    "incomplete_candidate_plan: target diff does not match "
                    "snapshots"
                )

    @staticmethod
    def _validate_replacement_change(
        change: PlanAdjustmentExerciseReplacementChange,
        before_exercise: PlanAdjustmentExerciseSnapshot,
        after_exercise: PlanAdjustmentExerciseSnapshot,
    ) -> None:
        before_identity = (
            before_exercise.exercise_id,
            before_exercise.exercise_name,
        )
        after_identity = (
            after_exercise.exercise_id,
            after_exercise.exercise_name,
        )
        if before_identity != (
            change.before.exercise_id,
            change.before.exercise_name,
        ) or after_identity != (
            change.after.exercise_id,
            change.after.exercise_name,
        ):
            raise ValueError(
                "incomplete_candidate_plan: replacement diff does not match "
                "snapshots"
            )


class ValidatedPlanAdjustmentProposal(_StrictFrozenModel):
    initial_status: Literal["pending_confirmation"]
    ttl_hours: int = Field(ge=1, le=72)
    created_at: datetime
    expires_at: datetime
    payload: PlanAdjustmentProposalPayload
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_lifecycle_window(self) -> Self:
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("proposal lifecycle timestamps require timezones")
        expected_expiry = self.created_at + timedelta(hours=self.ttl_hours)
        if self.expires_at != expected_expiry:
            raise ValueError("proposal expiry must match the selected TTL")
        return self


_PAYLOAD_ERROR_PRIORITY: tuple[
    PlanAdjustmentProposalPayloadErrorCode,
    ...,
] = (
    "payload_not_object",
    "forbidden_field",
    "missing_base_fingerprint",
    "incomplete_candidate_plan",
    "unsupported_change_type",
    "no_effect_change",
    "invalid_plan_bounds",
    "invalid_target",
)


def plan_adjustment_proposal_payload_error_codes(
    payload: Any,
) -> tuple[PlanAdjustmentProposalPayloadErrorCode, ...]:
    """Validate a v1 payload and project Pydantic failures to stable codes."""

    if not isinstance(payload, dict):
        return ("payload_not_object",)

    try:
        PlanAdjustmentProposalPayload.model_validate(payload)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        has_invalid_change_item = any(
            (location := tuple(str(item) for item in error["loc"]))[:1]
            == ("changes",)
            and location != ("changes",)
            for error in errors
        )
        invalid_exercise_collections = {
            location[:2]
            for error in errors
            if len(
                location := tuple(str(item) for item in error["loc"])
            )
            > 2
            and location[:2]
            in {("before", "exercises"), ("after", "exercises")}
        }
        detected: set[PlanAdjustmentProposalPayloadErrorCode] = set()
        for error in errors:
            location = tuple(str(item) for item in error["loc"])
            error_type = error["type"]
            message = error["msg"]

            if error_type == "extra_forbidden":
                detected.add("forbidden_field")
            elif location[:2] == ("target", "base_plan_fingerprint"):
                detected.add("missing_base_fingerprint")
            elif location[:2] == ("target", "resource_type"):
                detected.add("invalid_target")
            elif error_type == "union_tag_invalid" and location[:1] == (
                "changes",
            ):
                detected.add("unsupported_change_type")
            elif (
                has_invalid_change_item
                and location == ("changes",)
                and error_type == "too_short"
            ):
                continue
            elif "no_effect_change" in message:
                detected.add("no_effect_change")
            elif "invalid_plan_bounds" in message or (
                location[:1] in {("before",), ("after",)}
                and error_type
                in {
                    "greater_than_equal",
                    "less_than_equal",
                    "string_too_long",
                    "string_too_short",
                }
            ):
                detected.add("invalid_plan_bounds")
            elif (
                error_type == "too_short"
                and location[:2] in invalid_exercise_collections
            ):
                continue
            elif "incomplete_candidate_plan" in message or (
                location[:2] in {
                    ("before", "exercises"),
                    ("after", "exercises"),
                }
                and (
                    error_type == "missing"
                    or (
                        error_type == "too_short"
                        and location[:2] not in invalid_exercise_collections
                    )
                )
            ):
                detected.add("incomplete_candidate_plan")
            else:
                detected.add("forbidden_field")

        return tuple(
            code for code in _PAYLOAD_ERROR_PRIORITY if code in detected
        )
    return ()
