import pytest
from unittest.mock import patch, AsyncMock
from app.config import settings


async def get_token(client, email):
    resp = await client.post("/api/v1/auth/register", json={"email": email, "password": "pass1234"})
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_chat_creates_new_session(client):
    token = await get_token(client, "chat1@example.com")
    with patch("app.routers.chat.chat_with_agent", new=AsyncMock(return_value="你好！")):
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "你好"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "你好！"
    assert data["session_id"] is not None


@pytest.mark.asyncio
async def test_chat_with_existing_session(client):
    token = await get_token(client, "chat2@example.com")
    with patch("app.routers.chat.chat_with_agent", new=AsyncMock(return_value="继续")):
        r1 = await client.post("/api/v1/chat", json={"message": "第一条"}, headers={"Authorization": f"Bearer {token}"})
        session_id = r1.json()["session_id"]
        r2 = await client.post(
            "/api/v1/chat",
            json={"message": "第二条", "session_id": session_id},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r2.status_code == 200
    assert r2.json()["session_id"] == session_id


@pytest.mark.asyncio
async def test_list_sessions(client):
    token = await get_token(client, "chat3@example.com")
    with patch("app.routers.chat.chat_with_agent", new=AsyncMock(return_value="ok")):
        await client.post("/api/v1/chat", json={"message": "msg1"}, headers={"Authorization": f"Bearer {token}"})
        await client.post("/api/v1/chat", json={"message": "msg2"}, headers={"Authorization": f"Bearer {token}"})
    resp = await client.get("/api/v1/chat/sessions", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(resp.json()) >= 2


@pytest.mark.asyncio
async def test_get_messages_wrong_session_returns_404(client):
    token = await get_token(client, "chat4@example.com")
    resp = await client.get(
        "/api/v1/chat/sessions/nonexistent-id/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_returns_503_when_ai_is_not_configured(client):
    token = await get_token(client, "chat-unconfigured@example.com")
    with patch.object(settings, "DEEPSEEK_API_KEY", ""):
        resp = await client.post(
            "/api/v1/chat",
            json={"message": "帮我安排训练"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 503
    assert "DEEPSEEK_API_KEY" in resp.json()["detail"]
