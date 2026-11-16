from app.models.agent import AgentArtifact, AgentRun
from app.models.exercise import Exercise
from app.models.food import Food, FoodAlias
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
        source_name="产品目录",
        source_reference="catalog-v1",
    )
    assert f.calories_per_100g == 165.0
    assert f.diet_tags == ["高蛋白"]
    assert f.source_name == "产品目录"
    alias = FoodAlias(
        food_id="food-id",
        alias="鸡肉胸",
        normalized_alias="鸡肉胸",
    )
    assert alias.food_id == "food-id"


def test_knowledge_chunk_model_fields():
    k = KnowledgeChunk(
        source="NSCA教材",
        topic="渐进超负荷",
        content="渐进超负荷原则是力量训练的基础理论之一。",
    )
    assert k.topic == "渐进超负荷"
    assert k.embedding is None


def test_agent_run_persists_v5_intent_semantics():
    columns = AgentRun.__table__.columns

    assert "intent_domain" in columns
    assert "request_kind" in columns
    assert "requested_effect" in columns
    assert "change_requests" in columns
    assert "evidence_requirements" in columns
    assert "requested_output" in columns
    assert columns["understanding_version"].server_default.arg == "v6"


def test_daily_meal_artifact_has_owned_versioned_payload_fields():
    columns = AgentArtifact.__table__.columns

    assert "user_id" in columns
    assert "conversation_id" in columns
    assert "source_run_id" in columns
    assert "payload_fingerprint" in columns
    assert "context_fingerprints" in columns
    assert "expires_at" in columns
