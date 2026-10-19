import httpx
from app.config import settings
from app.services.ai_client import AIServiceError


async def get_embedding(text: str) -> list[float]:
    if not settings.EMBEDDING_BASE_URL or not settings.EMBEDDING_MODEL:
        raise AIServiceError(
            "RAG 尚未配置：请设置兼容 OpenAI Embeddings API 的地址和模型"
        )

    headers = {}
    if settings.EMBEDDING_API_KEY:
        headers["Authorization"] = f"Bearer {settings.EMBEDDING_API_KEY}"
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.EMBEDDING_BASE_URL.rstrip('/')}/embeddings",
                headers=headers,
                json={"model": settings.EMBEDDING_MODEL, "input": text},
                timeout=30.0,
            )
            response.raise_for_status()
            embedding = response.json()["data"][0]["embedding"]
    except httpx.HTTPStatusError as exc:
        raise AIServiceError(
            f"Embedding 服务请求失败（HTTP {exc.response.status_code}）"
        ) from exc
    except httpx.RequestError as exc:
        raise AIServiceError("暂时无法连接 Embedding 服务") from exc
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIServiceError("Embedding 服务返回了无法识别的数据") from exc

    if len(embedding) != settings.EMBEDDING_DIM:
        raise AIServiceError(
            f"Embedding 维度不匹配：期望 {settings.EMBEDDING_DIM}，实际 {len(embedding)}"
        )
    return embedding
