import httpx
import pytest

from app.config import settings
from app.services import wechat


@pytest.mark.asyncio
async def test_exchange_code_requires_server_credentials(monkeypatch):
    monkeypatch.setattr(settings, "WECHAT_APP_ID", "")
    monkeypatch.setattr(settings, "WECHAT_APP_SECRET", "")

    with pytest.raises(wechat.WeChatConfigurationError):
        await wechat.exchange_code("temporary-code")


@pytest.mark.asyncio
async def test_exchange_code_returns_only_stable_identity(monkeypatch):
    monkeypatch.setattr(settings, "WECHAT_APP_ID", "wx-test-app")
    monkeypatch.setattr(settings, "WECHAT_APP_SECRET", "server-only-secret")
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["secret"] == "server-only-secret"
        assert request.url.params["js_code"] == "temporary-code"
        return httpx.Response(
            200,
            json={
                "openid": "openid-123",
                "unionid": "union-123",
                "session_key": "must-not-leave-server",
            },
        )

    def client_factory(*args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(handler),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(wechat.httpx, "AsyncClient", client_factory)
    result = await wechat.exchange_code("temporary-code")

    assert result == wechat.WeChatSession(
        app_id="wx-test-app", open_id="openid-123", union_id="union-123"
    )
    assert not hasattr(result, "session_key")


@pytest.mark.asyncio
async def test_exchange_code_maps_invalid_code(monkeypatch):
    monkeypatch.setattr(settings, "WECHAT_APP_ID", "wx-test-app")
    monkeypatch.setattr(settings, "WECHAT_APP_SECRET", "server-only-secret")
    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"errcode": 40029, "errmsg": "invalid code"}
                )
            ),
            timeout=kwargs.get("timeout"),
        )

    monkeypatch.setattr(wechat.httpx, "AsyncClient", client_factory)

    with pytest.raises(wechat.WeChatInvalidCodeError):
        await wechat.exchange_code("expired-code")
