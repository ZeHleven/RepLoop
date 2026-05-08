from pydantic import BaseModel
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

    model_config = {"from_attributes": True}
