from datetime import date, datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from app.services.training_lifecycle import training_today


class MealItemCreate(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    food_id: Optional[str] = None
    custom_food_id: str | None = None
    custom_food_version: int | None = Field(default=None, ge=1)
    food_name: str = Field(min_length=1, max_length=100)
    amount_g: float = Field(gt=0, le=10000)
    calories: float = Field(ge=0, le=50000)
    protein_g: float = Field(default=0.0, ge=0, le=5000)
    carbs_g: float = Field(default=0.0, ge=0, le=5000)
    fat_g: float = Field(default=0.0, ge=0, le=5000)

    @model_validator(mode='after')
    def one_food_source(self):
        if self.food_id and self.custom_food_id:
            raise ValueError('标准食品与私人食品来源不能同时指定')
        if bool(self.custom_food_id) != (self.custom_food_version is not None):
            raise ValueError('私人食品引用必须包含有效版本')
        return self

    @field_validator("food_name")
    @classmethod
    def normalize_food_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("食物名称不能为空")
        return normalized


class MealItemResponse(BaseModel):
    id: str
    meal_id: str
    food_id: Optional[str] = None
    custom_food_id: str | None = None
    custom_food_version: int | None = None
    food_name: str
    amount_g: float
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float

    model_config = {"from_attributes": True}


class MealLogCreate(BaseModel):
    logged_at: date
    meal_type: Literal["早餐", "午餐", "晚餐", "加餐"] = "早餐"
    items: list[MealItemCreate] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def reject_future_date(self):
        if self.logged_at > training_today():
            raise ValueError("不能记录未来的饮食")
        return self


class MealLogUpdate(MealLogCreate):
    """A complete replacement candidate for an existing meal."""


class MealLogResponse(BaseModel):
    id: str
    user_id: str
    logged_at: date
    meal_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class MealLogDetail(MealLogResponse):
    items: list[MealItemResponse] = []


class DailySummary(BaseModel):
    date: date
    total_calories: float
    total_protein_g: float
    total_carbs_g: float
    total_fat_g: float
    meals: list[MealLogDetail]


class TodaySummary(DailySummary):
    energy_estimate: dict | None = None


class NutritionAdviceResponse(BaseModel):
    advice: str
