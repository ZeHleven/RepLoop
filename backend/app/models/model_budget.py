"""Durable lifetime allowance; monetary amounts are integer nano-yuan."""
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ModelBudget(Base):
    __tablename__ = "model_budgets"
    __table_args__ = (
        CheckConstraint("spent_nano >= 0 AND reserved_nano >= 0", name="ck_model_budget_nonnegative"),
    )
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), primary_key=True)
    spent_nano: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    reserved_nano: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelCharge(Base):
    __tablename__ = "model_charges"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey("model_budgets.user_id"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    pricing_version: Mapped[str] = mapped_column(String(80))
    reserved_nano: Mapped[int] = mapped_column(BigInteger)
    actual_nano: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
