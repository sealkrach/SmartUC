from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (create_token, generate_api_key, get_current_user, hash_api_key, hash_password, require_role,
                    verify_password)
from ..db import get_session
from ..models import ApiKey, Role, User
from ..schemas import ApiKeyCreated, ApiKeyOut, Token, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@router.post("/login", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends(), session: AsyncSession = Depends(get_session)):
    user = await session.scalar(select(User).where(User.email == form.username.lower().strip()))
    if not user or not verify_password(form.password, user.password_hash) or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    return Token(access_token=create_token(user))


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@users_router.get("", response_model=list[UserOut])
async def list_users(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return (await session.scalars(select(User).order_by(User.created_at))).all()


@users_router.post("", response_model=UserOut, status_code=201)
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session),
                      _: User = Depends(require_role(Role.admin))):
    if payload.role not in {r.value for r in Role}:
        raise HTTPException(400, "Invalid role")
    if await session.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(409, "Email already registered")
    user = User(email=payload.email.lower(), full_name=payload.full_name, role=payload.role,
                password_hash=hash_password(payload.password))
    session.add(user)
    await session.commit()
    return user


@users_router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, payload: UserUpdate, session: AsyncSession = Depends(get_session),
                      _: User = Depends(require_role(Role.admin))):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if payload.full_name is not None:
        user.full_name = payload.full_name
    if payload.role is not None:
        if payload.role not in {r.value for r in Role}:
            raise HTTPException(400, "Invalid role")
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password:
        user.password_hash = hash_password(payload.password)
    await session.commit()
    return user


# ---------------------------------------------------------------- personal API keys (MCP / scripts)

@users_router.get("/me/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    return (await session.scalars(select(ApiKey).where(ApiKey.user_id == user.id, ApiKey.revoked.is_(False))
                                  .order_by(ApiKey.created_at.desc()))).all()


@users_router.post("/me/api-keys", response_model=ApiKeyCreated, status_code=201)
async def create_api_key(body: dict, session: AsyncSession = Depends(get_session),
                         user: User = Depends(get_current_user)):
    """Returns the plaintext key once; only its hash is stored."""
    key = generate_api_key()
    row = ApiKey(user_id=user.id, name=str(body.get("name") or "MCP client")[:100], prefix=key[:10],
                 key_hash=hash_api_key(key))
    session.add(row)
    await session.commit()
    return ApiKeyCreated(id=row.id, name=row.name, prefix=row.prefix, created_at=row.created_at,
                         last_used_at=None, key=key)


@users_router.delete("/me/api-keys/{key_id}", status_code=204)
async def revoke_api_key(key_id: str, session: AsyncSession = Depends(get_session),
                         user: User = Depends(get_current_user)):
    row = await session.get(ApiKey, key_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "API key not found")
    row.revoked = True
    await session.commit()
