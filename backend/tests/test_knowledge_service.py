import pytest
from unittest.mock import patch, AsyncMock
from app.models.knowledge import EMBEDDING_DIM
from app.services.knowledge import add_knowledge_chunk, search_knowledge_base


def _unit_vector(index: int) -> list[float]:
    """One-hot unit vector: cosine_distance(i, j) = 0 if i==j else 1."""
    vec = [0.0] * EMBEDDING_DIM
    vec[index] = 1.0
    return vec


@pytest.mark.asyncio
async def test_add_knowledge_chunk_persists(db_session):
    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=_unit_vector(0))):
        chunk = await add_knowledge_chunk(
            db_session,
            source="test_source",
            topic="nutrition",
            content="Protein is essential for muscle repair.",
        )

    assert chunk.id is not None
    assert chunk.source == "test_source"
    assert chunk.topic == "nutrition"
    assert len(chunk.embedding) == EMBEDDING_DIM


@pytest.mark.asyncio
async def test_search_returns_chunks_ordered_by_similarity(db_session):
    target_vec = _unit_vector(10)
    noise_vec = _unit_vector(11)  # orthogonal to target_vec → cosine_distance = 1

    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=target_vec)):
        chunk_a = await add_knowledge_chunk(
            db_session, source="s", topic="fitness", content="Target content"
        )

    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=noise_vec)):
        chunk_b = await add_knowledge_chunk(
            db_session, source="s", topic="fitness", content="Noise content"
        )

    # Search with query matching chunk_a exactly (cosine_distance=0 for chunk_a, 1 for chunk_b)
    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=target_vec)):
        results = await search_knowledge_base(db_session, query_text="query", limit=2)

    assert len(results) == 2
    assert results[0].id == chunk_a.id


@pytest.mark.asyncio
async def test_search_filter_by_topic(db_session):
    vec = _unit_vector(20)

    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=vec)):
        await add_knowledge_chunk(db_session, source="s", topic="nutrition", content="A")
        await add_knowledge_chunk(db_session, source="s", topic="exercise", content="B")

    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=vec)):
        results = await search_knowledge_base(db_session, query_text="q", topic="nutrition", limit=10)

    topics = {r.topic for r in results}
    assert "nutrition" in topics
    assert "exercise" not in topics


@pytest.mark.asyncio
async def test_search_respects_limit(db_session):
    vec = _unit_vector(30)
    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=vec)):
        for i in range(5):
            await add_knowledge_chunk(db_session, source="s", topic="misc", content=f"Chunk {i}")

    with patch("app.services.knowledge.get_embedding", new=AsyncMock(return_value=vec)):
        results = await search_knowledge_base(db_session, query_text="q", limit=3)

    assert len(results) <= 3
