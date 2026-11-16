"""Anonymous demo identities are usable only in an explicitly isolated deployment."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import uuid

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models.demo import DemoVisit, DemoIdentity
from app.services.auth import create_access_token, create_refresh_token
from app.services.demo_seed import SCENARIO_NAME, SCENARIO_DESCRIPTION, QUICK_PROMPTS, seed_demo_user

VISIT_LOCK = 18734001
BUDGET_LOCK = 18734002


def require_demo() -> None:
    if not settings.DEMO_ENABLED:
        raise HTTPException(404, "演示入口未启用")


def validate_demo_database() -> None:
    if settings.DEMO_ENABLED:
        name = make_url(settings.DATABASE_URL).database or ""
        if not name.endswith(("_demo", "_demo_test")):
            raise RuntimeError("DEMO_ENABLED requires a separate database ending in _demo or _demo_test")


async def visit_for_user(db: AsyncSession, user_id: str, *, active: bool = True) -> DemoVisit:
    visit = await db.scalar(select(DemoVisit).join(
        DemoIdentity, DemoIdentity.visit_id == DemoVisit.id
    ).where(DemoIdentity.user_id == user_id).execution_options(populate_existing=True))
    if visit is None or visit.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(401, "演示体验已到期，请重新进入")
    if active and visit.current_user_id != user_id:
        raise HTTPException(401, "演示已重新开始，请返回当前体验")
    return visit


def session_response(visit: DemoVisit) -> dict:
    return {"access_token": create_access_token(visit.current_user_id),
        "refresh_token": create_refresh_token(visit.current_user_id), "token_type": "bearer",
        "expires_at": visit.expires_at, "scenario_name": SCENARIO_NAME,
        "scenario_description": SCENARIO_DESCRIPTION, "quick_prompts": QUICK_PROMPTS,
        "resets_remaining": max(0, settings.DEMO_MAX_RESETS - visit.reset_count),
        "model_available": bool(settings.DEEPSEEK_API_KEY and settings.DEMO_TOTAL_BUDGET_NANO > 0
                                and settings.DEMO_USER_BUDGET_NANO > 0)}


async def create_visit(db: AsyncSession, request_key: str, client_host: str) -> dict:
    require_demo()
    # Serialize creation and reset, including duplicate/retried submissions.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": VISIT_LOCK})
    key_hash = hashlib.sha256(request_key.encode()).hexdigest()
    existing = await db.scalar(select(DemoVisit).where(DemoVisit.key_hash == key_hash))
    if existing:
        if existing.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(401, "演示体验已到期，请重新进入")
        return session_response(existing)
    now = datetime.now(timezone.utc)
    # Trust the socket peer, never arbitrary client-supplied forwarding headers.
    client_hash = hmac.new(settings.SECRET_KEY.encode(), client_host.encode(), hashlib.sha256).hexdigest()
    total = await db.scalar(select(func.count()).select_from(DemoVisit))
    recent = await db.scalar(select(func.count()).select_from(DemoVisit).where(
        DemoVisit.client_hash == client_hash, DemoVisit.created_at > now - timedelta(hours=1)))
    if total >= settings.DEMO_MAX_VISITS or recent >= settings.DEMO_VISITS_PER_HOUR:
        raise HTTPException(429, "当前体验名额已满，请稍后再试")
    user = await seed_demo_user(db)
    visit = DemoVisit(id=str(uuid.uuid4()), key_hash=key_hash, client_hash=client_hash,
        budget_user_id=user.id, current_user_id=user.id, reset_count=0,
        expires_at=now + timedelta(hours=settings.DEMO_SESSION_HOURS), created_at=now)
    db.add(visit)
    await db.flush()
    db.add(DemoIdentity(user_id=user.id, visit_id=visit.id))
    await db.commit()
    return session_response(visit)


async def reset_visit(db: AsyncSession, user_id: str) -> dict:
    require_demo()
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": VISIT_LOCK})
    visit = await visit_for_user(db, user_id)
    if visit.reset_count >= settings.DEMO_MAX_RESETS:
        raise HTTPException(429, "本次体验的重新开始次数已用完")
    user = await seed_demo_user(db)
    db.add(DemoIdentity(user_id=user.id, visit_id=visit.id))
    visit.current_user_id = user.id
    visit.reset_count += 1
    # Previous users and their queued/running results remain in their own history.
    # Neither expiry nor the original billing identity is renewed by resetting.
    await db.commit()
    return session_response(visit)
