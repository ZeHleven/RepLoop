from pydantic import BaseModel, Field, field_validator
from typing import Any
from app.schemas.exercise_energy import EnergyCategory


class CustomExerciseCreate(BaseModel):
    model_config = {'extra': 'forbid'}
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    muscles: list[str] = Field(default_factory=list, max_length=10)
    equipment: list[str] = Field(default_factory=list, max_length=10)
    contraindications: list[str] = Field(default_factory=list, max_length=20)
    energy_category: EnergyCategory | None = None

    @field_validator('name', 'description')
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError('请填写名称与动作方法')
        return value.strip()

    @field_validator('muscles', 'equipment', 'contraindications')
    @classmethod
    def labels(cls, values):
        if any(not value.strip() or len(value.strip()) > 50 for value in values):
            raise ValueError('标签必须为 1–50 字')
        return list(dict.fromkeys(value.strip() for value in values))


class ExerciseResponse(BaseModel):
    body_parts: list[str] = Field(default_factory=list)
    search_aliases: list[str] = Field(default_factory=list)
    counting_note: str | None = None
    id: str
    name_zh: str
    name_en: str
    category: str
    muscle_primary: Any
    muscle_secondary: Any
    equipment: Any
    difficulty: str
    movement_pattern: str | None
    rep_range_min: int | None
    rep_range_max: int | None
    sets_range_min: int | None
    sets_range_max: int | None
    technique_cues: str | None
    common_mistakes: str | None
    contraindications: Any
    video_url: str | None
    is_active: bool

    model_config = {"from_attributes": True}
