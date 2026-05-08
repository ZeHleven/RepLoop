import httpx
from app.config import settings


async def get_embedding(text: str) -> list[float]:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.EMBEDDING_BASE_URL}/v1/embeddings",
            headers={"Authorization": f"Bearer {settings.EMBEDDING_API_KEY}"},
            json={"model": settings.EMBEDDING_MODEL, "input": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
