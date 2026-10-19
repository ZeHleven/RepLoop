import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models.user import User
from app.models.profile import UserProfile
from app.models.wechat import WeChatIdentity
from app.schemas.auth import (
    LoginRequest, RefreshRequest, RegisterRequest, TokenResponse,
    WeChatLoginRequest, WeChatLoginResponse,
)
from app.services.auth import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token
)
from app.services.wechat import (
    WeChatConfigurationError,
    WeChatInvalidCodeError,
    WeChatSession,
    WeChatUnavailableError,
    exchange_code,
)
from jose import JWTError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)):
    if body.email:
        existing = await db.scalar(select(User).where(User.email == body.email))
        if existing:
            raise HTTPException(status_code=400, detail="邮箱已注册")
    if body.phone:
        existing = await db.scalar(select(User).where(User.phone == body.phone))
        if existing:
            raise HTTPException(status_code=400, detail="手机号已注册")

    user = User(
        email=body.email,
        phone=body.phone,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.flush()

    profile = UserProfile(user_id=user.id)
    db.add(profile)
    await db.commit()

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = None
    if body.email:
        user = await db.scalar(select(User).where(User.email == body.email))
    elif body.phone:
        user = await db.scalar(select(User).where(User.phone == body.phone))

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


async def _find_wechat_identity(
    session: WeChatSession, db: AsyncSession
) -> WeChatIdentity | None:
    identity = await db.scalar(
        select(WeChatIdentity).where(
            WeChatIdentity.app_id == session.app_id,
            WeChatIdentity.open_id == session.open_id,
        )
    )
    if identity is None and session.union_id:
        identity = await db.scalar(
            select(WeChatIdentity)
            .where(WeChatIdentity.union_id == session.union_id)
            .order_by(WeChatIdentity.created_at.asc())
        )
    return identity


@router.post("/wechat", response_model=WeChatLoginResponse)
async def wechat_login(
    body: WeChatLoginRequest, db: AsyncSession = Depends(get_db)
):
    try:
        wechat_session = await exchange_code(body.code)
    except WeChatInvalidCodeError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except WeChatConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WeChatUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    identity = await _find_wechat_identity(wechat_session, db)
    is_new_user = False
    now = datetime.now(timezone.utc)

    if identity is None:
        user = User(password_hash=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
        await db.flush()
        profile = UserProfile(user_id=user.id)
        identity = WeChatIdentity(
            user_id=user.id,
            app_id=wechat_session.app_id,
            open_id=wechat_session.open_id,
            union_id=wechat_session.union_id,
            last_login_at=now,
        )
        db.add_all([profile, identity])
        is_new_user = True
    else:
        user = await db.get(User, identity.user_id)
        if user is None:
            raise HTTPException(status_code=500, detail="微信身份关联异常")
        profile = await db.scalar(
            select(UserProfile).where(UserProfile.user_id == user.id)
        )
        if profile is None:
            profile = UserProfile(user_id=user.id)
            db.add(profile)

        if (
            identity.app_id != wechat_session.app_id
            or identity.open_id != wechat_session.open_id
        ):
            identity = WeChatIdentity(
                user_id=user.id,
                app_id=wechat_session.app_id,
                open_id=wechat_session.open_id,
                union_id=wechat_session.union_id,
                last_login_at=now,
            )
            db.add(identity)
        else:
            identity.last_login_at = now
            if wechat_session.union_id and not identity.union_id:
                identity.union_id = wechat_session.union_id

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        identity = await db.scalar(
            select(WeChatIdentity).where(
                WeChatIdentity.app_id == wechat_session.app_id,
                WeChatIdentity.open_id == wechat_session.open_id,
            )
        )
        if identity is None:
            raise HTTPException(status_code=409, detail="微信身份绑定冲突")
        user = await db.get(User, identity.user_id)
        profile = await db.scalar(
            select(UserProfile).where(UserProfile.user_id == identity.user_id)
        )
        if user is None or profile is None:
            raise HTTPException(status_code=500, detail="微信身份关联异常")
        is_new_user = False

    return WeChatLoginResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        is_new_user=is_new_user,
        onboarding_completed=profile.onboarding_completed,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    try:
        user_id = decode_token(body.refresh_token, expected_type="refresh")
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的 refresh token")

    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )
