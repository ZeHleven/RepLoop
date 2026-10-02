"""Bounded conversational requirements, never business facts or write authority."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RequirementEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=400)
    remove: bool = False


class TaskUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["new", "continue", "resume", "cancel", "none"]
    task_id: str = Field(default="", max_length=100)
    trigger: str = Field(default="", max_length=400)
    requirements: list[RequirementEdit] = Field(default_factory=list, max_length=12)


class TaskRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=40)
    quote: str = Field(min_length=1, max_length=400)
    source_run_id: str = Field(min_length=1, max_length=100)
    kind: Literal["goal", "workflow"] = "goal"
    as_of: str = Field(default="", max_length=10)


PlanChangeField = Literal["exercise.sets", "exercise.reps", "exercise.rest_seconds", "exercise.recommended_weight_kg",
                          "schedule.duration_weeks", "schedule.days_per_week"]


class PlanInputGap(BaseModel):
    """An unresolved user requirement, not an inferred plan default."""
    model_config = ConfigDict(extra="forbid")
    field_path: PlanChangeField | None = None
    target_reference: str | None = Field(default=None, max_length=120)
    quote: str = Field(min_length=1, max_length=400)


class PendingPlanWithdrawal(BaseModel):
    """Withdraw an unapplied field, never delete a business resource."""
    model_config = ConfigDict(extra="forbid")
    field_path: PlanChangeField
    target_reference: str | None = Field(default=None, max_length=120)
    quote: str = Field(min_length=1, max_length=400)


class PendingMealItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    food_id: str = Field(min_length=1, max_length=120)
    food_name: str = Field(min_length=1, max_length=100)
    amount_g: float = Field(gt=0, allow_inf_nan=False)


class PendingMealDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logged_at: str = Field(min_length=10, max_length=10)
    meal_type: Literal["早餐", "午餐", "晚餐", "加餐"]
    items: list[PendingMealItem] = Field(min_length=1, max_length=20)


class PendingPlanChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: Literal["workout_plan"] = "workout_plan"
    operation: Literal["update"] = "update"
    field_path: PlanChangeField
    target_reference: str | None = Field(default=None, max_length=120)
    value: str | int | float
    preserve_unspecified: bool = True


class ConversationTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    request: str = Field(max_length=4000)
    requirements: list[TaskRequirement] = Field(default_factory=list, max_length=12)
    phase: Literal["working", "responded", "awaiting_input", "awaiting_confirmation", "cancelled", "failed", "blocked"] = "working"
    last_run_id: str = Field(max_length=100)
    artifact_id: str | None = None
    proposal_id: str | None = None
    proposal_pending: bool = False
    pending_plan_changes: list[PendingPlanChange] = Field(default_factory=list, max_length=12)
    pending_meal: PendingMealDraft | None = None
    # Identified goals before a proposal exists; never proof of an executable draft.
    unproposed_plan_changes: list[PendingPlanChange] = Field(default_factory=list, max_length=12)
    plan_input_gaps: list[PlanInputGap] = Field(default_factory=list, max_length=12)


class TaskSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    active_task_id: str | None = None
    tasks: list[ConversationTask] = Field(default_factory=list, max_length=3)
    transition: Literal["new", "continue", "resume", "cancel", "none", "untracked"] = "untracked"
    evicted_task_ids: list[str] = Field(default_factory=list, max_length=3)
