from pydantic import BaseModel
from datetime import datetime, date


# ── Planned Exercise ──────────────────────────────────────────────────────────

class PlannedExerciseCreate(BaseModel):
    exercise_id: str
    day_of_week: int
    sets: int = 3
    reps: str = "10"
    rest_seconds: int = 90
    order_index: int = 0


class PlannedExerciseResponse(BaseModel):
    id: str
    plan_id: str
    exercise_id: str
    day_of_week: int
    sets: int
    reps: str
    rest_seconds: int
    order_index: int
    model_config = {"from_attributes": True}


# ── Workout Plan ──────────────────────────────────────────────────────────────

class WorkoutPlanCreate(BaseModel):
    name: str
    goal: str | None = None
    duration_weeks: int = 4
    days_per_week: int = 3
    notes: str | None = None
    exercises: list[PlannedExerciseCreate] = []


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
    model_config = {"from_attributes": True}


class WorkoutPlanDetail(WorkoutPlanResponse):
    exercises: list[PlannedExerciseResponse] = []


# ── Workout Session ───────────────────────────────────────────────────────────

class SetData(BaseModel):
    reps: int
    weight_kg: float | None = None


class SessionExerciseCreate(BaseModel):
    exercise_id: str
    sets_data: list[SetData]


class WorkoutSessionCreate(BaseModel):
    trained_at: date
    plan_id: str | None = None
    duration_min: int | None = None
    notes: str | None = None
    exercises: list[SessionExerciseCreate] = []


class SessionExerciseResponse(BaseModel):
    id: str
    session_id: str
    exercise_id: str
    sets_data: list
    model_config = {"from_attributes": True}


class WorkoutSessionResponse(BaseModel):
    id: str
    user_id: str
    plan_id: str | None
    trained_at: date
    duration_min: int | None
    notes: str | None
    created_at: datetime
    model_config = {"from_attributes": True}


class WorkoutSessionDetail(WorkoutSessionResponse):
    exercises: list[SessionExerciseResponse] = []


# ── AI Generate ───────────────────────────────────────────────────────────────

class GeneratePlanRequest(BaseModel):
    goal: str | None = None
    duration_weeks: int = 4
