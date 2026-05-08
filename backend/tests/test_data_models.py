from app.models.exercise import Exercise
from app.models.food import Food
from app.models.knowledge import KnowledgeChunk


def test_exercise_model_fields():
    e = Exercise(
        name_zh="杠铃深蹲",
        name_en="Barbell Back Squat",
        category="力量",
        muscle_primary=["股四头肌", "臀大肌"],
        difficulty="中级",
        movement_pattern="蹲",
    )
    assert e.name_zh == "杠铃深蹲"
    assert e.muscle_primary == ["股四头肌", "臀大肌"]


def test_food_model_fields():
    f = Food(
        name_zh="鸡胸肉",
        name_en="Chicken Breast",
        category="蛋白质",
        calories_per_100g=165.0,
        protein_g=31.0,
        carbs_g=0.0,
        fat_g=3.6,
        diet_tags=["高蛋白"],
    )
    assert f.calories_per_100g == 165.0
    assert f.diet_tags == ["高蛋白"]


def test_knowledge_chunk_model_fields():
    k = KnowledgeChunk(
        source="NSCA教材",
        topic="渐进超负荷",
        content="渐进超负荷原则是力量训练的基础理论之一。",
    )
    assert k.topic == "渐进超负荷"
    assert k.embedding is None
