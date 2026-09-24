from pydantic import BaseModel, Field, field_validator, model_validator
from datetime import datetime, date
from typing import Annotated, Literal


PlanExplanation = Annotated[str, Field(min_length=1, max_length=1000)]


# ── Planned Exercise ──────────────────────────────────────────────────────────

class PlannedExerciseCreate(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=100)
    day_of_week: int = Field(ge=1, le=7)
    sets: int = Field(default=3, ge=1, le=8)
    reps: str = Field(default="10", min_length=1, max_length=20)
    rest_seconds: int = Field(default=90, ge=15, le=600)
    order_index: int = Field(default=0, ge=0, le=49)


class PlannedExerciseResponse(BaseModel):
    id: str
    plan_id: str
    exercise_id: str
    exercise_name: str | None = None
    safety_notice: str | None = None
    day_of_week: int
    sets: int
    reps: str
    rest_seconds: int
    recommended_weight_kg: float | None = None
    order_index: int
    model_config = {"from_attributes": True}


# ── Workout Plan ──────────────────────────────────────────────────────────────

class WorkoutPlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal: str | None = Field(default=None, max_length=50)
    duration_weeks: int = Field(default=4, ge=2, le=12)
    days_per_week: int = Field(default=3, ge=1, le=7)
    notes: str | None = Field(default=None, max_length=5000)
    exercises: list[PlannedExerciseCreate] = Field(default_factory=list, max_length=50)


class WeeklySessionReference(BaseModel):
    day_of_week: int
    session_id: str
    status: Literal['in_progress', 'completed']


class WorkoutPlanResponse(BaseModel):
    id: str
    user_id: str
    name: str
    goal: str | None
    duration_weeks: int
    days_per_week: int
    is_active: bool
    ai_generated: bool
    notes: str | None
    created_at: datetime
    safety_status: Literal["compatible", "needs_review"] = "compatible"
    safety_reasons: list[str] = Field(default_factory=list)
    manual_proposals_enabled: bool = False
    display_name: str | None = None
    week_start: date | None = None
    weekly_completed_days: int = 0
    weekly_sessions: list[WeeklySessionReference] = Field(default_factory=list)
    model_config = {"from_attributes": True}


class WorkoutPlanDetail(WorkoutPlanResponse):
    exercises: list[PlannedExerciseResponse] = Field(default_factory=list)


# ── Personalized Plan Draft ──────────────────────────────────────────────────

class _PersonalizedSchedule(BaseModel):
    # Optional for older clients. When supplied, dates are explicit, never inferred.
    training_days: list[Annotated[int, Field(strict=True, ge=1, le=7)]] | None = Field(
        default=None, min_length=1, max_length=7,
    )

    @field_validator("training_days")
    @classmethod
    def validate_training_days(cls, values: list[int] | None) -> list[int] | None:
        if values is not None:
            if len(values) != len(set(values)):
                raise ValueError("训练日不能重复")
            return sorted(values)
        return None


class PersonalizedPlanPreviewRequest(_PersonalizedSchedule):
    goal: str | None = Field(default=None, max_length=50)
    duration_weeks: int = Field(default=4, ge=2, le=12)
    days_per_week: int | None = Field(default=None, ge=1, le=7)
    session_duration_min: int | None = Field(default=None, ge=20, le=120)

    @model_validator(mode="after")
    def validate_schedule_count(self):
        if self.training_days is not None and self.days_per_week is not None:
            if len(self.training_days) != self.days_per_week:
                raise ValueError("训练日数量与每周天数不一致")
        return self


class PersonalizedExerciseOption(BaseModel):
    body_parts: list[str] = Field(default_factory=list)
    search_aliases: list[str] = Field(default_factory=list)
    counting_note: str | None = None
    energy_category: Literal['resistance_training', 'bodyweight_resistance'] | None = None
    energy_category_version: int = 0
    exercise_id: str = Field(min_length=1, max_length=100)
    exercise_name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=30)
    difficulty: str = Field(min_length=1, max_length=20)
    equipment: list[str] = Field(default_factory=list)
    safety_notice: str | None = None


class PersonalizedPlanExercise(BaseModel):
    exercise_id: str = Field(min_length=1, max_length=100)
    exercise_name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=30)
    safety_notice: str | None = None
    day_of_week: int = Field(ge=1, le=7)
    sets: int = Field(ge=1, le=8)
    reps: str = Field(min_length=1, max_length=20)
    rest_seconds: int = Field(ge=15, le=600)
    order_index: int = Field(ge=0, le=20)


class PersonalizedPlanPreview(BaseModel):
    name: str
    goal: str
    duration_weeks: int
    days_per_week: int
    session_duration_min: int
    rationale: list[PlanExplanation] = Field(default_factory=list)
    safety_notes: list[PlanExplanation] = Field(default_factory=list)
    exercises: list[PersonalizedPlanExercise] = Field(default_factory=list)
    exercise_options: list[PersonalizedExerciseOption] = Field(default_factory=list)
    generation_strategy: str = "profile_rules_v1"


class PersonalizedPlanConfirmRequest(_PersonalizedSchedule):
    name: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=1, max_length=50)
    duration_weeks: int = Field(ge=2, le=12)
    days_per_week: int = Field(ge=1, le=7)
    session_duration_min: int = Field(ge=20, le=120)
    rationale: list[PlanExplanation] = Field(default_factory=list, max_length=12)
    safety_notes: list[PlanExplanation] = Field(default_factory=list, max_length=12)
    exercises: list[PersonalizedPlanExercise] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def validate_selected_schedule(self):
        if self.training_days is not None:
            if len(self.training_days) != self.days_per_week:
                raise ValueError("训练日数量与每周天数不一致")
            if {item.day_of_week for item in self.exercises} != set(self.training_days):
                raise ValueError("动作安排与所选训练日不一致，请重新编排")
        return self


# ── Workout Session ───────────────────────────────────────────────────────────

class SetData(BaseModel):
    reps: int = Field(ge=1, le=1000)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)


class SessionExerciseCreate(BaseModel):
    exercise_id: str
    sets_data: list[SetData]


class WorkoutSessionCreate(BaseModel):
    trained_at: date
    plan_id: str | None = None
    duration_min: int | None = None
    notes: str | None = None
    exercises: list[SessionExerciseCreate] = Field(default_factory=list)


class WorkoutSessionStart(BaseModel):
    plan_id: str
    day_of_week: int = Field(ge=1, le=7)


class WorkoutSetRecord(BaseModel):
    reps: int = Field(ge=1, le=1000)
    weight_kg: float | None = Field(default=None, ge=0, le=1000)
    finished_at: datetime | None = None

    @field_validator('finished_at')
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError('计时时间必须包含时区')
        return value


class WorkoutRestRecord(BaseModel):
    model_config = {'extra': 'forbid'}
    event_id: str = Field(min_length=8, max_length=100)
    actual_rest_seconds: int = Field(ge=0, le=86400)
    end_reason: Literal['next_set', 'workout_ended']


class WorkoutFeedback(BaseModel):
    difficulty_feedback: Literal["too_easy", "just_right", "too_hard"] | None = None
    perceived_exertion: int | None = Field(default=None, ge=1, le=10)
    energy_level: int | None = Field(default=None, ge=1, le=5)
    pain_level: int = Field(default=0, ge=0, le=10)
    pain_areas: list[str] = Field(default_factory=list, max_length=8)
    feedback_notes: str | None = Field(default=None, max_length=1000)

    @field_validator("pain_areas")
    @classmethod
    def normalize_pain_areas(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 50 for item in normalized):
            raise ValueError("疼痛部位描述过长")
        return normalized

    @model_validator(mode="after")
    def validate_pain_context(self):
        if self.pain_level > 0 and not self.pain_areas:
            raise ValueError("记录疼痛时请选择疼痛部位")
        if self.pain_level == 0 and self.pain_areas:
            raise ValueError("疼痛等级为 0 时不应填写疼痛部位")
        return self


class WorkoutSessionComplete(WorkoutFeedback):
    duration_min: int | None = Field(default=None, ge=1, le=1440)
    notes: str | None = Field(default=None, max_length=2000)


class SessionExerciseResponse(BaseModel):
    energy_category: Literal['resistance_training', 'bodyweight_resistance'] | None = None
    energy_category_source: str | None = None
    energy_rule_version: str | None = None
    energy_classification_editable: bool = False
    library_energy_category: Literal['resistance_training', 'bodyweight_resistance'] | None = None
    library_energy_category_version: int | None = None
    library_energy_editable: bool = False
    id: str
    session_id: str
    exercise_id: str
    exercise_name: str | None = None
    safety_notice: str | None = None
    order_index: int
    target_sets: int | None
    target_reps: str | None
    target_weight_kg: float | None = None
    rest_seconds: int | None
    sets_data: list[dict] = Field(default_factory=list)
    previous_sets_data: list[dict] = Field(default_factory=list)
    personal_best_weight_kg: float | None = None
    personal_best_reps: int | None = None
    model_config = {"from_attributes": True}


class WorkoutSessionResponse(BaseModel):
    id: str
    user_id: str
    plan_id: str | None
    day_of_week: int | None
    status: str
    trained_at: date
    duration_min: int | None
    notes: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


class WorkoutAdjustmentResponse(BaseModel):
    exercise_id: str
    exercise_name: str
    action: str
    before: dict
    after: dict
    reason: str
    safety_priority: bool = False


class AdaptiveAdjustmentProposalResponse(BaseModel):
    id: str
    proposal_type: Literal["plan_adjustment_v2"]
    status: Literal[
        "pending_confirmation", "applied", "rejected", "expired", "stale", "failed"
    ]
    version: int = Field(ge=1)
    expires_at: datetime
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkoutSessionDetail(WorkoutSessionResponse):
    energy_classification_version: int = 0
    plan_name: str | None = None
    orphaned: bool = False
    total_sets: int = 0
    total_reps: int = 0
    total_volume_kg: float = 0
    exercises: list[SessionExerciseResponse] = Field(default_factory=list)
    feedback: WorkoutFeedback | None = None
    adjustments: list[WorkoutAdjustmentResponse] = Field(default_factory=list)
    adaptive_adjustment_status: Literal[
        "not_needed", "pending_confirmation", "applied", "rejected",
        "expired", "stale", "failed", "blocked_by_existing",
    ] = "not_needed"
    adaptive_adjustment_proposal: AdaptiveAdjustmentProposalResponse | None = None


class WeeklyWorkoutProgress(BaseModel):
    week_start: date
    week_end: date | None = None
    is_complete: bool | None = None
    sessions: int = 0
    sets: int = 0
    reps: int = 0
    volume_kg: float = 0


class DailyWorkoutProgress(BaseModel):
    date: date
    sessions: int = 0
    sets: int = 0
    reps: int = 0
    volume_kg: float = 0


class WorkoutProgressResponse(BaseModel):
    weeks: int
    total_sessions: int
    total_sets: int
    total_reps: int
    total_volume_kg: float
    weekly: list[WeeklyWorkoutProgress]
    selected_week: date | None = None
    daily: list[DailyWorkoutProgress] = Field(default_factory=list)
    as_of: date | None = None
    timezone: str = "Asia/Shanghai"
    averages_per_calendar_week: dict[str, float] = Field(default_factory=dict)
    average_denominator_weeks: int = 0


# ── AI Generate ───────────────────────────────────────────────────────────────

class GeneratePlanRequest(BaseModel):
    goal: str | None = None
    duration_weeks: int = 4
