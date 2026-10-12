from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from sqlalchemy import select

from .models import ApiKey, Role, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user: User) -> str:
    s = get_settings()
    payload = {
        "sub": user.id,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=s.access_token_expire_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


API_KEY_PREFIX = "uch_"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


async def resolve_token(session: AsyncSession, token: str) -> User | None:
    """Accept either a session JWT or a personal API key (uch_...). Returns None when invalid."""
    if token.startswith(API_KEY_PREFIX):
        key = await session.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(token),
                                                        ApiKey.revoked.is_(False)))
        if not key or not key.user.is_active:
            return None
        key.last_used_at = datetime.now(timezone.utc)
        await session.commit()
        return key.user
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    user = await session.get(User, payload.get("sub"))
    return user if user and user.is_active else None


async def get_current_user(
    token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_session)
) -> User:
    user = await resolve_token(session, token)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return user


ROLE_RANK = {Role.requester.value: 0, Role.reviewer.value: 1, Role.admin.value: 2}


def require_role(minimum: Role):
    async def dep(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum.value]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role {minimum.value} or higher")
        return user

    return dep


def can_edit(user: User, owner_id: str | None) -> bool:
    return user.role in (Role.admin.value, Role.reviewer.value) or user.id == owner_id
