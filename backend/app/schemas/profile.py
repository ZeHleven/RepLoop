from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ProfileUpdateRequest(BaseModel):
    daily_activity_level: Literal['sedentary', 'walking', 'physical_work'] | None = None
    age: int | None = Field(default=None, ge=12, le=100)
    gender: Literal["male", "female", "prefer_not_to_say"] | None = None
    height_cm: float | None = Field(default=None, ge=100, le=250)
    weight_kg: float | None = Field(default=None, ge=25, le=350)
    experience_level: str | None = None
    primary_goal: str | None = None
    training_days_per_week: int | None = Field(default=None, ge=1, le=7)
    session_duration_min: int | None = Field(default=None, ge=10, le=300)
    training_location: str | None = None
    diet_restriction: str | None = None
    injuries: list[str] | None = Field(default=None, max_length=12)
    chronic_conditions: list[str] | None = Field(default=None, max_length=12)
    onboarding_completed: bool | None = None

    @field_validator("injuries", "chronic_conditions")
    @classmethod
    def normalize_health_values(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(len(item) > 50 for item in normalized):
            raise ValueError("健康筛查项目过长")
        return normalized


class ProfileResponse(BaseModel):
    daily_activity_level: Literal['sedentary', 'walking', 'physical_work'] | None = None
    user_id: str
    age: int | None
    gender: Literal["male", "female", "prefer_not_to_say"] | None
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
    injuries: list[str]
    chronic_conditions: list[str]
    onboarding_completed: bool

    @field_validator("injuries", "chronic_conditions", mode="before")
    @classmethod
    def normalize_health_lists(cls, value: object) -> list[str] | object:
        return [] if value is None else value

    model_config = {"from_attributes": True}


class ProfileUpdateResponse(ProfileResponse):
    active_plan_safety_status: Literal["compatible", "needs_review"] | None = None
    active_plan_safety_reasons: list[str] = Field(default_factory=list)


class WeightLogRequest(BaseModel):
    weight_kg: float = Field(ge=25, le=350)


class WeightLogResponse(BaseModel):
    id: str
    weight_kg: float
    recorded_at: datetime

    model_config = {"from_attributes": True}
