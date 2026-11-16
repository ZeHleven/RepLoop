import uuid
from sqlalchemy import String, Text, Integer, Boolean, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    owner_id: Mapped[str | None] = mapped_column(String, ForeignKey("users.id"), nullable=True, index=True)
    name_zh: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(30))
    energy_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    energy_category_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    muscle_primary: Mapped[list] = mapped_column(JSONB, default=list)
    muscle_secondary: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    equipment: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    difficulty: Mapped[str] = mapped_column(String(10))
    movement_pattern: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rep_range_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rep_range_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sets_range_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sets_range_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    technique_cues: Mapped[str | None] = mapped_column(Text, nullable=True)
    common_mistakes: Mapped[str | None] = mapped_column(Text, nullable=True)
    contraindications: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    video_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ExerciseCatalogImport(Base):
    """Internal import ownership, so rollback cannot remove pre-existing/user-referenced records."""
    __tablename__ = 'exercise_catalog_imports'
    catalog_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    exercise_id: Mapped[str] = mapped_column(String, ForeignKey('exercises.id'), unique=True)
    created_by_import: Mapped[bool] = mapped_column(Boolean)
    initial_snapshot: Mapped[dict] = mapped_column(JSONB)
