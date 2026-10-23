"""Exercise real HTTP policy and durable identities without external services."""
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.database import get_db
from app.main import app
from app.models.model_budget import ModelBudget, ModelCharge
from app.models.profile import UserProfile
from app.models.user import User
from app.models.wechat import WeChatIdentity
from app.services.auth import hash_password
from app.services import model_budget
from app.services.wechat import WeChatSession

pytestmark = pytest.mark.asyncio
MESSAGE = "邮箱和手机号注册已关闭，请使用微信登录。已有账号仍可登录。"


async def counts(db):
    return [await db.scalar(select(func.count()).select_from(model))
            for model in (User, UserProfile, WeChatIdentity, ModelBudget, ModelCharge)]


@pytest.mark.parametrize("identity", [
    {"email": "disabled-email@example.com"},
    {"phone": "19900000001"},
    {"email": "disabled-both@example.com", "phone": "19900000002"},
    {"email": "invalid-email"},
    {"phone": "not-a-phone"},
    {},
    {"email": "disabled-bypass@example.com", "provider": "wechat", "enabled": True},
])
async def test_register_rejects_all_identity_inputs_without_writes(client, db_session, identity):
    before = await counts(db_session)
    response = await client.post("/api/v1/auth/register", json={
        **identity, "password": "synthetic-password-123",
    })
    assert response.status_code == 403
    assert response.json() == {"detail": MESSAGE}
    assert await counts(db_session) == before


@pytest.mark.parametrize("content", [None, b'{"email":', b'not-json'])
async def test_register_rejects_missing_or_malformed_body(client, db_session, content):
    before = await counts(db_session)
    response = await client.post("/api/v1/auth/register", content=content,
                                 headers={"Content-Type": "application/json"})
    assert response.status_code == 403
    assert response.json()["detail"] == MESSAGE
    assert await counts(db_session) == before


async def test_register_does_not_open_database_session(client):
    async def fail_database():
        raise AssertionError("disabled registration must not access the database")
        yield  # pragma: no cover

    original = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = fail_database
    try:
        response = await client.post("/api/v1/auth/register", json={
            "email": "disabled-no-db@example.com", "password": "synthetic-password-123",
        })
        assert response.status_code == 403
    finally:
        app.dependency_overrides[get_db] = original


@pytest.mark.parametrize("kind", ["email", "phone"])
async def test_existing_password_identity_login_refresh_and_budget_unchanged(
    client, db_session, session_factory, monkeypatch, kind,
):
    monkeypatch.setattr(model_budget, "AsyncSessionLocal", session_factory)
    identity = ({"email": "existing-policy@example.com"} if kind == "email"
                else {"phone": "19900000003"})
    password = "existing-synthetic-password"
    user = User(**identity, password_hash=hash_password(password))
    db_session.add(user)
    await db_session.flush()
    db_session.add_all([UserProfile(user_id=user.id), ModelBudget(
        user_id=user.id, spent_nano=4_000_000_000, reserved_nano=100_000_000,
    )])
    await db_session.commit()
    before = await counts(db_session)
    denied = await client.post("/api/v1/auth/register", json={**identity, "password": password})
    assert denied.status_code == 403 and denied.json()["detail"] == MESSAGE
    logged_in = await client.post("/api/v1/auth/login", json={**identity, "password": password})
    assert logged_in.status_code == 200
    tokens = logged_in.json()
    headers = {"Authorization": "Bearer " + tokens["access_token"]}
    me = await client.get("/api/v1/me", headers=headers)
    assert me.json()["id"] == user.id
    budget = await client.get("/api/v1/agent/budget", headers=headers)
    assert budget.status_code == 200
    assert budget.json()["spent_yuan"] == "4.000000000"
    assert budget.json()["remaining_yuan"] == "0.900000000"
    refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 200
    wrong = await client.post("/api/v1/auth/login", json={**identity, "password": "wrong-password"})
    assert wrong.status_code == 401
    authenticated_registration = await client.post("/api/v1/auth/register/", headers=headers,
        json={"email": "blocked-authenticated@example.com", "password": password}, follow_redirects=True)
    assert authenticated_registration.status_code == 403
    assert await counts(db_session) == before


async def test_wechat_first_and_repeat_login_preserve_one_user_and_budget(
    client, db_session, session_factory, monkeypatch,
):
    monkeypatch.setattr(model_budget, "AsyncSessionLocal", session_factory)
    session = WeChatSession(app_id="synthetic-policy-app", open_id="policy-openid-" + uuid.uuid4().hex,
                            union_id=None)
    before = await counts(db_session)
    with patch("app.routers.auth.exchange_code", new=AsyncMock(return_value=session)):
        first = await client.post("/api/v1/auth/wechat", json={"code": "synthetic-first-code"})
        assert first.status_code == 200 and first.json()["is_new_user"] is True
        first_headers = {"Authorization": "Bearer " + first.json()["access_token"]}
        first_me = (await client.get("/api/v1/me", headers=first_headers)).json()
        db_session.add(ModelBudget(user_id=first_me["id"], spent_nano=5_000_000_000))
        await db_session.commit()
        second = await client.post("/api/v1/auth/wechat", json={"code": "synthetic-repeat-code"})
        assert second.status_code == 200 and second.json()["is_new_user"] is False
        headers = {"Authorization": "Bearer " + second.json()["access_token"]}
        assert (await client.get("/api/v1/me", headers=headers)).json()["id"] == first_me["id"]
        budget = (await client.get("/api/v1/agent/budget", headers=headers)).json()
        assert budget["limit_yuan"] == "5.00" and budget["remaining_yuan"] == "0.000000000"
        with pytest.raises(model_budget.ModelBudgetError, match="5元"):
            await model_budget.reserve(first_me["id"], "deepseek-flash", 1)
    after = await counts(db_session)
    assert after[:4] == [value + 1 for value in before[:4]]
    assert after[4] == before[4]
