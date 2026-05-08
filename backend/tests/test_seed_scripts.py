"""Tests for seed data correctness and import_knowledge CLI."""
import pytest
import tempfile
import os
from unittest.mock import patch, AsyncMock, MagicMock
from app.models.knowledge import EMBEDDING_DIM


def test_seed_exercises_has_20_items():
    from scripts.seed_exercises import EXERCISES
    assert len(EXERCISES) == 20
    for ex in EXERCISES:
        assert "name_zh" in ex
        assert "name_en" in ex
        assert "category" in ex
        assert "muscle_primary" in ex
        assert "difficulty" in ex


def test_seed_foods_has_20_items():
    from scripts.seed_foods import FOODS
    assert len(FOODS) == 20
    for food in FOODS:
        assert "name_zh" in food
        assert "name_en" in food
        assert "category" in food
        assert "calories_per_100g" in food
        assert "protein_g" in food


def test_import_knowledge_parse_chunks():
    from scripts.import_knowledge import parse_chunks

    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = parse_chunks(text)
    assert len(chunks) == 3
    assert chunks[0] == "First paragraph."
    assert chunks[2] == "Third paragraph."


def test_import_knowledge_skips_empty_paragraphs():
    from scripts.import_knowledge import parse_chunks

    text = "\n\nFirst.\n\n\n\nSecond.\n\n"
    chunks = parse_chunks(text)
    assert len(chunks) == 2


@pytest.mark.asyncio
async def test_import_knowledge_main(db_session):
    from scripts.import_knowledge import main as import_main

    vec = [0.0] * EMBEDDING_DIM
    vec[50] = 1.0

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("Chunk one about protein.\n\nChunk two about carbs.")
        tmp_path = f.name

    try:
        with patch("scripts.import_knowledge.create_async_engine") as mock_engine_fn, \
             patch("scripts.import_knowledge.async_sessionmaker") as mock_factory_cls, \
             patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=vec)):
            mock_engine = MagicMock()
            mock_engine.dispose = AsyncMock()
            mock_engine_fn.return_value = mock_engine
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(return_value=db_session)
            mock_cm.__aexit__ = AsyncMock(return_value=False)
            mock_factory_cls.return_value = MagicMock(return_value=mock_cm)

            await import_main(tmp_path, topic="nutrition", source="test")
    finally:
        os.unlink(tmp_path)
