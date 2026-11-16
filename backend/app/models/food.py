import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, String, Integer, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Food(Base):
    __tablename__ = "foods"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name_zh: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str | None] = mapped_column(String(100), nullable=True)
    category: Mapped[str] = mapped_column(String(30))
    calories_per_100g: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float, default=0.0)
    carbs_g: Mapped[float] = mapped_column(Float, default=0.0)
    fat_g: Mapped[float] = mapped_column(Float, default=0.0)
    fiber_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    common_portion_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    diet_tags: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_common_in_china: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    source_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    browse_category: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    browse_aliases: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    source_info: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class FoodCatalogImport(Base):
    __tablename__ = 'food_catalog_imports'
    catalog_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    food_id: Mapped[str] = mapped_column(String, ForeignKey('foods.id'), unique=True)
    created_by_import: Mapped[bool] = mapped_column(Boolean)
    initial_snapshot: Mapped[dict] = mapped_column(JSONB)


class FoodAlias(Base):
    __tablename__ = "food_aliases"

    id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    food_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("foods.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    alias: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )


class CustomFood(Base):
    """Private manual library. Never queried by the Agent standard-food tools."""
    __tablename__ = 'custom_foods'
    __table_args__ = (UniqueConstraint('user_id', 'client_request_id', name='uq_custom_food_request'),)

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String, ForeignKey('users.id'), index=True)
    name: Mapped[str] = mapped_column(String(100))
    amount_g: Mapped[float] = mapped_column(Float)
    calories: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float)
    carbs_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default='1')
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default='true')
    client_request_id: Mapped[str] = mapped_column(String(100))
    creation_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
