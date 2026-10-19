import pytest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models.wechat import WeChatIdentity
from app.services.wechat import (
    WeChatConfigurationError,
    WeChatInvalidCodeError,
    WeChatSession,
)


@pytest.mark.asyncio
async def test_register_with_email(client):
    resp = await client.post("/api/v1/auth/register", json={
        "email": "test@example.com",
        "password": "password123"
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_register_duplicate_email(client):
    payload = {"email": "dup@example.com", "password": "password123"}
    await client.post("/api/v1/auth/register", json=payload)
    resp = await client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 400
    assert "邮箱已注册" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_success(client):
    await client.post("/api/v1/auth/register", json={
        "email": "login@example.com", "password": "password123"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "login@example.com", "password": "password123"
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    await client.post("/api/v1/auth/register", json={
        "email": "wrong@example.com", "password": "password123"
    })
    resp = await client.post("/api/v1/auth/login", json={
        "email": "wrong@example.com", "password": "wrongpass"
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token(client):
    reg = await client.post("/api/v1/auth/register", json={
        "email": "refresh@example.com", "password": "password123"
    })
    refresh_token = reg.json()["refresh_token"]
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.asyncio
async def test_access_token_cannot_be_used_as_refresh_token(client):
    reg = await client.post("/api/v1/auth/register", json={
        "email": "wrong-token-type@example.com", "password": "password123"
    })
    access_token = reg.json()["access_token"]
    resp = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": access_token},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_refresh_token_cannot_access_protected_endpoint(client):
    reg = await client.post("/api/v1/auth/register", json={
        "email": "refresh-as-access@example.com", "password": "password123"
    })
    refresh_token = reg.json()["refresh_token"]
    resp = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_protected_endpoint_with_valid_token(client):
    reg = await client.post("/api/v1/auth/register", json={
        "email": "me@example.com", "password": "password123"
    })
    token = reg.json()["access_token"]
    resp = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == "me@example.com"


@pytest.mark.asyncio
async def test_protected_endpoint_without_token(client):
    resp = await client.get("/api/v1/me")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_wechat_login_creates_user_and_reuses_identity(client, db_session):
    session = WeChatSession(
        app_id="wx-test-app", open_id="openid-001", union_id="union-001"
    )
    with patch(
        "app.routers.auth.exchange_code", new=AsyncMock(return_value=session)
    ) as exchange:
        first = await client.post("/api/v1/auth/wechat", json={"code": "code-1"})
        second = await client.post("/api/v1/auth/wechat", json={"code": "code-2"})

    assert first.status_code == 200
    assert first.json()["is_new_user"] is True
    assert first.json()["onboarding_completed"] is False
    assert second.status_code == 200
    assert second.json()["is_new_user"] is False
    exchange.assert_awaited()

    first_me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {first.json()['access_token']}"},
    )
    second_me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {second.json()['access_token']}"},
    )
    assert first_me.json()["id"] == second_me.json()["id"]

    identities = (await db_session.execute(
        select(WeChatIdentity).where(WeChatIdentity.open_id == "openid-001")
    )).scalars().all()
    assert len(identities) == 1


@pytest.mark.asyncio
async def test_wechat_union_id_links_identity_across_apps(client, db_session):
    first_session = WeChatSession(
        app_id="wx-app-a", open_id="openid-a", union_id="shared-union"
    )
    second_session = WeChatSession(
        app_id="wx-app-b", open_id="openid-b", union_id="shared-union"
    )
    with patch(
        "app.routers.auth.exchange_code",
        new=AsyncMock(side_effect=[first_session, second_session]),
    ):
        first = await client.post("/api/v1/auth/wechat", json={"code": "code-a"})
        second = await client.post("/api/v1/auth/wechat", json={"code": "code-b"})

    first_me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {first.json()['access_token']}"},
    )
    second_me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {second.json()['access_token']}"},
    )
    assert first_me.json()["id"] == second_me.json()["id"]
    assert second.json()["is_new_user"] is False

    identities = (await db_session.execute(
        select(WeChatIdentity).where(WeChatIdentity.union_id == "shared-union")
    )).scalars().all()
    assert len(identities) == 2


@pytest.mark.asyncio
async def test_wechat_login_rejects_invalid_code(client):
    with patch(
        "app.routers.auth.exchange_code",
        new=AsyncMock(side_effect=WeChatInvalidCodeError("微信登录凭证已失效，请重试")),
    ):
        response = await client.post(
            "/api/v1/auth/wechat", json={"code": "expired-code"}
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "微信登录凭证已失效，请重试"


@pytest.mark.asyncio
async def test_wechat_login_reports_missing_server_configuration(client):
    with patch(
        "app.routers.auth.exchange_code",
        new=AsyncMock(side_effect=WeChatConfigurationError("微信登录尚未配置，请联系管理员")),
    ):
        response = await client.post(
            "/api/v1/auth/wechat", json={"code": "valid-looking-code"}
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "微信登录尚未配置，请联系管理员"
