import uuid
from datetime import datetime, date
from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey, Date, DateTime, Index, func, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB
from app.database import Base


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    family_id: Mapped[str] = mapped_column(String, index=True)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    goal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    duration_weeks: Mapped[int] = mapped_column(Integer, default=4)
    days_per_week: Mapped[int] = mapped_column(Integer, default=3)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __init__(self, **kwargs):
        kwargs.setdefault("id", str(uuid.uuid4()))
        kwargs.setdefault("family_id", kwargs["id"])
        super().__init__(**kwargs)


class PlannedExercise(Base):
    __tablename__ = "planned_exercises"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    plan_id: Mapped[str] = mapped_column(String, ForeignKey("workout_plans.id"), index=True)
    day_of_week: Mapped[int] = mapped_column(Integer)
    exercise_id: Mapped[str] = mapped_column(String, ForeignKey("exercises.id"))
    sets: Mapped[int] = mapped_column(Integer, default=3)
    reps: Mapped[str] = mapped_column(String(20), default="10")
    rest_seconds: Mapped[int] = mapped_column(Integer, default=90)
    recommended_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)

    def __init__(self, **kwargs):
        kwargs.setdefault("id", str(uuid.uuid4()))
        super().__init__(**kwargs)


class WorkoutSession(Base):
    __tablename__ = "workout_sessions"
    __table_args__ = (Index('ix_workout_sessions_week_context', 'user_id', 'plan_family_id', 'week_start', 'day_of_week'),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), index=True)
    plan_id: Mapped[str | None] = mapped_column(String, ForeignKey("workout_plans.id"), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    plan_family_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    week_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="completed", server_default="completed")
    energy_classification_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    trained_at: Mapped[date] = mapped_column(Date, index=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    feedback_data: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    adjustments_data: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    adaptive_proposal_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("agent_proposals.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __init__(self, **kwargs):
        kwargs.setdefault("id", str(uuid.uuid4()))
        super().__init__(**kwargs)


class SessionExercise(Base):
    __tablename__ = "session_exercises"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("workout_sessions.id"), index=True)
    exercise_id: Mapped[str] = mapped_column(String, ForeignKey("exercises.id"))
    exercise_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    energy_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    energy_category_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    energy_rule_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    target_sets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_reps: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    rest_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sets_data: Mapped[list] = mapped_column(JSONB)

    def __init__(self, **kwargs):
        kwargs.setdefault("id", str(uuid.uuid4()))
        super().__init__(**kwargs)
