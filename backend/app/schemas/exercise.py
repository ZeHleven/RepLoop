from pydantic import BaseModel
from typing import Any


class ExerciseResponse(BaseModel):
    id: str
    name_zh: str
    name_en: str
    category: str
    muscle_primary: Any
    muscle_secondary: Any
    equipment: Any
    difficulty: str
    movement_pattern: str | None
    rep_range_min: int | None
    rep_range_max: int | None
    sets_range_min: int | None
    sets_range_max: int | None
    technique_cues: str | None
    common_mistakes: str | None
    contraindications: Any
    video_url: str | None
    is_active: bool

    model_config = {"from_attributes": True}
