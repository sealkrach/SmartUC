from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import ApiKey, RevokedToken, Role, User
from .services.ratelimit import LoginGuard, SlidingWindow

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

_s = get_settings()
login_guard = LoginGuard(_s.login_max_failures, _s.login_window_seconds, _s.login_lockout_seconds)
ai_limiter = SlidingWindow(_s.ai_rate_limit_per_minute, 60)

# bcrypt of a random string: used so that unknown accounts cost the same time as wrong passwords
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def check_password_policy(password: str) -> None:
    s = get_settings()
    if len(password) < s.password_min_length:
        raise HTTPException(400, f"Password must be at least {s.password_min_length} characters")
    if password.lower() in {"password", "admin1234", "changeme", "usecasehub"} or password.isdigit():
        raise HTTPException(400, "Password is too common")


def create_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "role": user.role,
        "ver": user.token_version,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + timedelta(minutes=s.access_token_expire_minutes),
    }
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


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
    payload = decode_token(token)
    if not payload or not payload.get("jti"):
        return None
    if await session.get(RevokedToken, payload["jti"]):
        return None
    user = await session.get(User, payload.get("sub"))
    if not user or not user.is_active or user.token_version != payload.get("ver", -1):
        return None
    return user


async def revoke_token(session: AsyncSession, token: str) -> None:
    payload = decode_token(token)
    if payload and payload.get("jti"):
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        if not await session.get(RevokedToken, payload["jti"]):
            session.add(RevokedToken(jti=payload["jti"], user_id=payload.get("sub", ""), expires_at=exp))


GUEST_WRITE_ALLOWLIST = {"/api/auth/logout"}


async def get_current_user(
    request: Request, token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_session)
) -> User:
    user = await resolve_token(session, token)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if user.is_demo_guest and request.method not in ("GET", "HEAD", "OPTIONS") \
            and request.url.path not in GUEST_WRITE_ALLOWLIST:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "The public demo is read-only. Sign in to make changes.")
    return user


def create_guest_token(user: User, minutes: int = 120) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "role": user.role, "ver": user.token_version, "jti": uuid.uuid4().hex,
               "guest": True, "iat": now, "exp": now + timedelta(minutes=minutes)}
    return jwt.encode(payload, s.secret_key, algorithm="HS256")


ROLE_RANK = {Role.requester.value: 0, Role.reviewer.value: 1, Role.admin.value: 2}


def is_reviewer(user: User) -> bool:
    return ROLE_RANK.get(user.role, 0) >= ROLE_RANK[Role.reviewer.value]


def require_role(minimum: Role):
    async def dep(user: User = Depends(get_current_user)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum.value]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role {minimum.value} or higher")
        return user

    return dep


def can_edit(user: User, owner_id: str | None) -> bool:
    return is_reviewer(user) or user.id == owner_id


def require_owner_or_reviewer(user: User, owner_id: str | None, what: str = "this use case") -> None:
    if not can_edit(user, owner_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Only the owner or a reviewer can act on {what}")


def check_ai_quota(user: User) -> None:
    """Per-user quota on calls that trigger paid LLM work."""
    if not ai_limiter.hit(user.id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            f"AI quota exceeded: max {get_settings().ai_rate_limit_per_minute} AI calls per minute")


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]
