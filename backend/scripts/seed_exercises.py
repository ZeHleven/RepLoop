"""Seed 20 exercises into the database. Safe to re-run (skips existing by name_en)."""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import settings
from app.database import Base
from app.database_url import normalize_async_database_url
from app.models.exercise import Exercise

EXERCISES = [
    dict(name_zh="卧推", name_en="Bench Press", category="力量", muscle_primary=["chest"], muscle_secondary=["triceps", "front_deltoid"], equipment=["barbell", "bench"], difficulty="中级", movement_pattern="push", rep_range_min=6, rep_range_max=12, sets_range_min=3, sets_range_max=5),
    dict(name_zh="深蹲", name_en="Squat", category="力量", muscle_primary=["quads"], muscle_secondary=["glutes", "hamstrings"], equipment=["barbell"], difficulty="中级", movement_pattern="squat", rep_range_min=5, rep_range_max=10, sets_range_min=3, sets_range_max=5),
    dict(name_zh="硬拉", name_en="Deadlift", category="力量", muscle_primary=["back"], muscle_secondary=["glutes", "hamstrings"], equipment=["barbell"], difficulty="高级", movement_pattern="hinge", rep_range_min=3, rep_range_max=8, sets_range_min=3, sets_range_max=5),
    dict(name_zh="引体向上", name_en="Pull-Up", category="力量", muscle_primary=["back"], muscle_secondary=["biceps"], equipment=["pull_up_bar"], difficulty="中级", movement_pattern="pull", rep_range_min=5, rep_range_max=12, sets_range_min=3, sets_range_max=4),
    dict(name_zh="肩上推举", name_en="Overhead Press", category="力量", muscle_primary=["front_deltoid"], muscle_secondary=["triceps"], equipment=["barbell"], difficulty="中级", movement_pattern="push", rep_range_min=6, rep_range_max=10, sets_range_min=3, sets_range_max=4),
    dict(name_zh="俯卧撑", name_en="Push-Up", category="力量", muscle_primary=["chest"], muscle_secondary=["triceps"], equipment=["bodyweight"], difficulty="初级", movement_pattern="push", rep_range_min=10, rep_range_max=20, sets_range_min=3, sets_range_max=4),
    dict(name_zh="哑铃弯举", name_en="Dumbbell Curl", category="力量", muscle_primary=["biceps"], equipment=["dumbbell"], difficulty="初级", movement_pattern="pull", rep_range_min=10, rep_range_max=15, sets_range_min=3, sets_range_max=4),
    dict(name_zh="三头肌下压", name_en="Tricep Pushdown", category="力量", muscle_primary=["triceps"], equipment=["cable"], difficulty="初级", movement_pattern="push", rep_range_min=12, rep_range_max=15, sets_range_min=3, sets_range_max=4),
    dict(name_zh="腿举", name_en="Leg Press", category="力量", muscle_primary=["quads"], muscle_secondary=["glutes"], equipment=["leg_press_machine"], difficulty="初级", movement_pattern="squat", rep_range_min=10, rep_range_max=15, sets_range_min=3, sets_range_max=4),
    dict(name_zh="坐姿划船", name_en="Seated Cable Row", category="力量", muscle_primary=["back"], muscle_secondary=["biceps"], equipment=["cable"], difficulty="初级", movement_pattern="pull", rep_range_min=10, rep_range_max=15, sets_range_min=3, sets_range_max=4),
    dict(name_zh="哑铃侧平举", name_en="Dumbbell Lateral Raise", category="力量", muscle_primary=["side_deltoid"], equipment=["dumbbell"], difficulty="初级", movement_pattern="push", rep_range_min=12, rep_range_max=15, sets_range_min=3, sets_range_max=4),
    dict(name_zh="腿弯举", name_en="Leg Curl", category="力量", muscle_primary=["hamstrings"], equipment=["leg_curl_machine"], difficulty="初级", movement_pattern="hinge", rep_range_min=12, rep_range_max=15, sets_range_min=3, sets_range_max=4),
    dict(name_zh="小腿提踵", name_en="Standing Calf Raise", category="力量", muscle_primary=["calves"], equipment=["bodyweight"], difficulty="初级", movement_pattern="push", rep_range_min=15, rep_range_max=20, sets_range_min=3, sets_range_max=4),
    dict(name_zh="平板支撑", name_en="Plank", category="核心", muscle_primary=["core"], equipment=["bodyweight"], difficulty="初级", movement_pattern="isometric", rep_range_min=30, rep_range_max=60, sets_range_min=3, sets_range_max=4),
    dict(name_zh="仰卧起坐", name_en="Crunch", category="核心", muscle_primary=["core"], equipment=["bodyweight"], difficulty="初级", movement_pattern="flex", rep_range_min=15, rep_range_max=25, sets_range_min=3, sets_range_max=4),
    dict(name_zh="跑步机跑步", name_en="Treadmill Run", category="有氧", muscle_primary=["quads", "hamstrings"], equipment=["treadmill"], difficulty="初级", rep_range_min=20, rep_range_max=60, sets_range_min=1, sets_range_max=1),
    dict(name_zh="骑行", name_en="Stationary Bike", category="有氧", muscle_primary=["quads"], equipment=["stationary_bike"], difficulty="初级", rep_range_min=20, rep_range_max=60, sets_range_min=1, sets_range_max=1),
    dict(name_zh="罗马尼亚硬拉", name_en="Romanian Deadlift", category="力量", muscle_primary=["hamstrings"], muscle_secondary=["glutes", "back"], equipment=["barbell"], difficulty="中级", movement_pattern="hinge", rep_range_min=8, rep_range_max=12, sets_range_min=3, sets_range_max=4),
    dict(name_zh="保加利亚分腿蹲", name_en="Bulgarian Split Squat", category="力量", muscle_primary=["quads"], muscle_secondary=["glutes"], equipment=["dumbbell", "bench"], difficulty="中级", movement_pattern="squat", rep_range_min=8, rep_range_max=12, sets_range_min=3, sets_range_max=4),
    dict(name_zh="俯身划船", name_en="Bent-Over Row", category="力量", muscle_primary=["back"], muscle_secondary=["biceps", "rear_deltoid"], equipment=["barbell"], difficulty="中级", movement_pattern="pull", rep_range_min=6, rep_range_max=10, sets_range_min=3, sets_range_max=5),
]


async def main():
    engine = create_async_engine(
        normalize_async_database_url(settings.DATABASE_URL), echo=False
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        inserted = 0
        for data in EXERCISES:
            existing = await session.scalar(
                select(Exercise).where(Exercise.name_en == data["name_en"])
            )
            if existing:
                continue
            session.add(Exercise(**data))
            inserted += 1
        await session.commit()
        print(f"Inserted {inserted} exercises ({len(EXERCISES) - inserted} already existed).")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
