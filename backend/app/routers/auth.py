from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models.user import User
from app.models.profile import UserProfile
from app.schemas.auth import RegisterRequest, LoginRequest, TokenResponse, RefreshRequest
from app.services.auth import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token
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


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    try:
        user_id = decode_token(body.refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="无效的 refresh token")

    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )
