from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class MealItemCreate(BaseModel):
    food_id: Optional[str] = None
    food_name: str
    amount_g: float = Field(gt=0)
    calories: float = Field(ge=0)
    protein_g: float = 0.0
    carbs_g: float = 0.0
    fat_g: float = 0.0


class MealItemResponse(BaseModel):
    id: str
    meal_id: str
    food_id: Optional[str] = None
    food_name: str
    amount_g: float
    calories: float
    protein_g: float
    carbs_g: float
    fat_g: float

    model_config = {"from_attributes": True}


class MealLogCreate(BaseModel):
    logged_at: date
    meal_type: str = "早餐"
    items: list[MealItemCreate] = []


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


class NutritionAdviceResponse(BaseModel):
    advice: str
