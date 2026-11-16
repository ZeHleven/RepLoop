import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, Boolean, ForeignKey, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), unique=True, index=True
    )
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(32), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi_category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    experience_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    primary_goal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    training_days_per_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    training_location: Mapped[str | None] = mapped_column(String(50), nullable=True)
    diet_restriction: Mapped[str | None] = mapped_column(String(50), nullable=True)
    daily_activity_level: Mapped[str | None] = mapped_column(String(20), nullable=True)
    injuries: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    chronic_conditions: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    onboarding_completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WeightLog(Base):
    __tablename__ = "weight_logs"

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    weight_kg: Mapped[float] = mapped_column(Float)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
