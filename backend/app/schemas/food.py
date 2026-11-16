import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing import Any


class FoodResponse(BaseModel):
    id: str
    name_zh: str
    name_en: str | None
    category: str
    calories_per_100g: float
    protein_g: float
    carbs_g: float
    fat_g: float
    fiber_g: float | None
    common_portion_g: float | None
    diet_tags: Any
    is_common_in_china: bool
    is_active: bool
    # Additive attribution; old filters, identity and nutrition fields are unchanged.
    source_info: dict | None = None

    model_config = {"from_attributes": True}


class CustomFoodValues(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=100)
    amount_g: float = Field(gt=0, le=10000)
    calories: float = Field(ge=0, le=50000)
    protein_g: float = Field(ge=0, le=5000)
    carbs_g: float = Field(ge=0, le=5000)
    fat_g: float = Field(ge=0, le=5000)

    @field_validator('name')
    @classmethod
    def trim_name(cls, value):
        if not value.strip():
            raise ValueError('食品名称不能为空')
        return value.strip()

    @model_validator(mode='after')
    def normalized_values(self):
        for field in ('calories', 'protein_g', 'carbs_g', 'fat_g'):
            value = getattr(self, field) / self.amount_g * 100
            if not math.isfinite(value) or value > (50000 if field == 'calories' else 5000):
                raise ValueError('每 100 克营养超出有效范围，请核对克数和营养总量')
        return self


class CustomFoodCreate(CustomFoodValues):
    client_request_id: str = Field(min_length=8, max_length=100)


class CustomFoodUpdate(CustomFoodValues):
    version: int = Field(ge=1)


class LibraryFood(FoodResponse):
    browse_category: str | None = None
    source: Literal['standard', 'custom'] = 'standard'
    version: int | None = None
    basis: CustomFoodValues | None = None
