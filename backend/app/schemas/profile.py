from pydantic import BaseModel
from typing import Any


class ProfileUpdateRequest(BaseModel):
    age: int | None = None
    gender: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    experience_level: str | None = None
    primary_goal: str | None = None
    training_days_per_week: int | None = None
    session_duration_min: int | None = None
    training_location: str | None = None
    diet_restriction: str | None = None
    injuries: list[str] | None = None
    chronic_conditions: list[str] | None = None
    onboarding_completed: bool | None = None


class ProfileResponse(BaseModel):
    user_id: str
    age: int | None
    gender: str | None
    height_cm: float | None
    weight_kg: float | None
    bmi: float | None
    bmi_category: str | None
    experience_level: str | None
    primary_goal: str | None
    training_days_per_week: int | None
    session_duration_min: int | None
    training_location: str | None
    diet_restriction: str | None
    injuries: Any
    chronic_conditions: Any
    onboarding_completed: bool

    model_config = {"from_attributes": True}


class WeightLogRequest(BaseModel):
    weight_kg: float


class WeightLogResponse(BaseModel):
    id: str
    weight_kg: float
    recorded_at: str

    model_config = {"from_attributes": True}
