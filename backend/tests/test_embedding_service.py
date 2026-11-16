import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.embedding import get_embedding
from app.config import settings


@pytest.mark.asyncio
async def test_get_embedding_returns_vector():
    fake_embedding = [0.1] * settings.EMBEDDING_DIM
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"embedding": fake_embedding}]}

    with patch.object(settings, "EMBEDDING_BASE_URL", "https://embedding.example/v1"), \
         patch.object(settings, "EMBEDDING_MODEL", "embed-model"), \
         patch("app.services.embedding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await get_embedding("test text")

    assert len(result) == settings.EMBEDDING_DIM
    assert result[0] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_get_embedding_sends_correct_payload():
    fake_embedding = [0.0] * settings.EMBEDDING_DIM
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": [{"embedding": fake_embedding}]}

    with patch.object(settings, "EMBEDDING_BASE_URL", "https://embedding.example/v1"), \
         patch.object(settings, "EMBEDDING_MODEL", "embed-model"), \
         patch("app.services.embedding.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        await get_embedding("hello world")

        call_kwargs = mock_client.post.call_args
        assert call_kwargs.kwargs["json"]["input"] == "hello world"
        assert call_kwargs.kwargs["json"]["model"] == settings.EMBEDDING_MODEL


@pytest.mark.asyncio
async def test_get_embedding_requires_configuration():
    from app.services.ai_client import AIServiceError

    with patch.object(settings, "EMBEDDING_BASE_URL", ""), \
         patch.object(settings, "EMBEDDING_MODEL", ""):
        with pytest.raises(AIServiceError, match="RAG 尚未配置"):
            await get_embedding("hello")
