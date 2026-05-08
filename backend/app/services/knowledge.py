from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pgvector.sqlalchemy import Vector
from app.models.knowledge import KnowledgeChunk, EMBEDDING_DIM
from app.services.embedding import get_embedding


async def add_knowledge_chunk(
    db: AsyncSession,
    *,
    source: str,
    topic: str,
    content: str,
) -> KnowledgeChunk:
    embedding = await get_embedding(content)
    chunk = KnowledgeChunk(source=source, topic=topic, content=content, embedding=embedding)
    db.add(chunk)
    await db.commit()
    await db.refresh(chunk)
    return chunk


async def search_knowledge_base(
    db: AsyncSession,
    *,
    query_text: str,
    topic: str | None = None,
    limit: int = 5,
) -> list[KnowledgeChunk]:
    query_embedding = await get_embedding(query_text)
    stmt = (
        select(KnowledgeChunk)
        .order_by(KnowledgeChunk.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    if topic:
        stmt = stmt.where(KnowledgeChunk.topic == topic)
    result = await db.execute(stmt)
    return list(result.scalars().all())
