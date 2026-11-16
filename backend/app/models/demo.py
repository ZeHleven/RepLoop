"""Visitor identity survives resetting the synthetic business data."""
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class DemoVisit(Base):
    __tablename__ = "demo_visits"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    client_hash: Mapped[str] = mapped_column(String(64), index=True)
    budget_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), unique=True)
    current_user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"))
    reset_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DemoIdentity(Base):
    __tablename__ = "demo_identities"
    user_id: Mapped[str] = mapped_column(String, ForeignKey("users.id"), primary_key=True)
    visit_id: Mapped[str] = mapped_column(String, ForeignKey("demo_visits.id"), index=True)
