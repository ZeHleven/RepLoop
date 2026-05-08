import uuid
from sqlalchemy import String, Text, Integer, Boolean
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name_zh: Mapped[str] = mapped_column(String(100))
    name_en: Mapped[str] = mapped_column(String(100))
    category: Mapped[str] = mapped_column(String(30))
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
