import uuid
from datetime import datetime, date
from sqlalchemy import String, Text, Integer, Float, ForeignKey, Date, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class MealLog(Base):
    __tablename__ = "meal_logs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    logged_at: Mapped[date] = mapped_column(Date, index=True)
    meal_type: Mapped[str] = mapped_column(String(20))  # 早餐|午餐|晚餐|加餐
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __init__(self, **kwargs):
        kwargs.setdefault("id", str(uuid.uuid4()))
        super().__init__(**kwargs)


class MealItem(Base):
    __tablename__ = "meal_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    meal_id: Mapped[str] = mapped_column(String, ForeignKey("meal_logs.id"), index=True)
    food_id: Mapped[str | None] = mapped_column(String, ForeignKey("foods.id"), nullable=True)
    food_name: Mapped[str] = mapped_column(String(100))
    amount_g: Mapped[float] = mapped_column(Float)
    calories: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float, default=0.0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0.0)
    fat_g: Mapped[float] = mapped_column(Float, default=0.0)

    def __init__(self, **kwargs):
        kwargs.setdefault("id", str(uuid.uuid4()))
        super().__init__(**kwargs)
