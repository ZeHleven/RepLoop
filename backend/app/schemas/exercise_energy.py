from typing import Literal

from pydantic import BaseModel, Field, model_validator

EnergyCategory = Literal['resistance_training', 'bodyweight_resistance']


class EnergyCategoryUpdate(BaseModel):
    model_config = {'extra': 'forbid'}
    energy_category: EnergyCategory | None
    expected_version: int = Field(ge=0, strict=True)


class SessionEnergyItem(BaseModel):
    model_config = {'extra': 'forbid'}
    session_exercise_id: str = Field(min_length=1, max_length=100)
    energy_category: EnergyCategory
    expected_exercise_version: int | None = Field(default=None, ge=0, strict=True)


class SessionEnergyUpdate(BaseModel):
    model_config = {'extra': 'forbid'}
    expected_version: int = Field(ge=0, strict=True)
    update_future: bool = Field(default=False, strict=True)
    exercises: list[SessionEnergyItem] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_targets(self):
        if len({row.session_exercise_id for row in self.exercises}) != len(self.exercises):
            raise ValueError('训练动作不能重复提交')
        if self.update_future and any(row.expected_exercise_version is None for row in self.exercises):
            raise ValueError('同步今后分类时必须提供动作库版本')
        return self
