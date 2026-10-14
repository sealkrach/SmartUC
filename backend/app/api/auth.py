from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (_DUMMY_HASH, check_password_policy, client_ip, create_token, generate_api_key, get_current_user,
                    hash_api_key, hash_password, login_guard, oauth2_scheme, require_role, revoke_token,
                    verify_password)
from ..db import get_session
from ..models import ApiKey, Role, User
from ..schemas import ApiKeyCreated, ApiKeyOut, PasswordChange, Token, UserCreate, UserOut, UserUpdate
from ..services import audit

router = APIRouter(prefix="/auth", tags=["auth"])
users_router = APIRouter(prefix="/users", tags=["users"])


@router.post("/login", response_model=Token)
async def login(request: Request, form: OAuth2PasswordRequestForm = Depends(),
                session: AsyncSession = Depends(get_session)):
    email = form.username.lower().strip()[:255]
    ip = client_ip(request)
    keys = (f"acct:{email}", f"ip:{ip}")
    if login_guard.is_locked(*keys):
        await audit.record(session, None, "auth.login", details={"email": email, "reason": "locked_out"},
                           outcome="denied")
        await session.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed attempts; try again later")
    user = await session.scalar(select(User).where(User.email == email))
    # constant-time path: always run a bcrypt check
    ok = verify_password(form.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        locked = login_guard.register_failure(*keys)
        await audit.record(session, None, "auth.login", details={"email": email, "locked": locked},
                           outcome="failure")
        await session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    login_guard.register_success(*keys)
    await audit.record(session, user, "auth.login", target_type="user", target_id=user.id)
    await session.commit()
    return Token(access_token=create_token(user))


@router.post("/logout", status_code=204)
async def logout(token: str = Depends(oauth2_scheme), session: AsyncSession = Depends(get_session),
                 user: User = Depends(get_current_user)):
    await revoke_token(session, token)
    await audit.record(session, user, "auth.logout", target_type="user", target_id=user.id)
    await session.commit()


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/me/password", status_code=204)
async def change_password(payload: PasswordChange, session: AsyncSession = Depends(get_session),
                          user: User = Depends(get_current_user)):
    if not verify_password(payload.current_password, user.password_hash):
        await audit.record(session, user, "auth.password_change", target_type="user", target_id=user.id,
                           outcome="failure")
        await session.commit()
        raise HTTPException(400, "Current password is incorrect")
    check_password_policy(payload.new_password)
    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1  # invalidates every existing session token
    user.must_change_password = False
    await audit.record(session, user, "auth.password_change", target_type="user", target_id=user.id)
    await session.commit()


# ---------------------------------------------------------------- users (admin)

@users_router.get("", response_model=list[UserOut])
async def list_users(session: AsyncSession = Depends(get_session), _: User = Depends(require_role(Role.admin))):
    return (await session.scalars(select(User).order_by(User.created_at))).all()


@users_router.post("", response_model=UserOut, status_code=201)
async def create_user(payload: UserCreate, session: AsyncSession = Depends(get_session),
                      admin: User = Depends(require_role(Role.admin))):
    if payload.role not in {r.value for r in Role}:
        raise HTTPException(400, "Invalid role")
    check_password_policy(payload.password)
    if await session.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(409, "Email already registered")
    user = User(email=payload.email.lower(), full_name=payload.full_name, role=payload.role,
                password_hash=hash_password(payload.password), must_change_password=True)
    session.add(user)
    await session.flush()
    await audit.record(session, admin, "user.create", target_type="user", target_id=user.id,
                       details={"email": user.email, "role": user.role})
    await session.commit()
    return user


async def _admin_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(User)
                                .where(User.role == Role.admin.value, User.is_active.is_(True))) or 0


@users_router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, payload: UserUpdate, session: AsyncSession = Depends(get_session),
                      admin: User = Depends(require_role(Role.admin))):
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    changes: dict = {}
    losing_admin = user.role == Role.admin.value and (
        (payload.role is not None and payload.role != Role.admin.value) or payload.is_active is False)
    if losing_admin and await _admin_count(session) <= 1:
        raise HTTPException(400, "Cannot remove the last active admin")
    if payload.full_name is not None:
        user.full_name = payload.full_name
        changes["full_name"] = True
    if payload.role is not None:
        if payload.role not in {r.value for r in Role}:
            raise HTTPException(400, "Invalid role")
        changes["role"] = [user.role, payload.role]
        user.role = payload.role
        user.token_version += 1
    if payload.is_active is not None:
        changes["is_active"] = payload.is_active
        user.is_active = payload.is_active
        if not payload.is_active:
            user.token_version += 1
    if payload.password:
        check_password_policy(payload.password)
        user.password_hash = hash_password(payload.password)
        user.must_change_password = True
        user.token_version += 1
        changes["password_reset"] = True
    await audit.record(session, admin, "user.update", target_type="user", target_id=user.id, details=changes)
    await session.commit()
    return user


@users_router.post("/{user_id}/anonymize", response_model=UserOut)
async def anonymize_user(user_id: str, session: AsyncSession = Depends(get_session),
                         admin: User = Depends(require_role(Role.admin))):
    """GDPR erasure for a departed user: pseudonymise identity, deactivate, revoke keys and sessions.
    Audit rows and decision logs keep the opaque user id so accountability is preserved."""
    user = await session.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Use another admin account to anonymise yourself")
    if user.role == Role.admin.value and user.is_active and await _admin_count(session) <= 1:
        raise HTTPException(400, "Cannot anonymise the last active admin")
    from datetime import datetime, timezone

    user.email = f"deleted-{user.id}@anonymized.invalid"
    user.full_name = ""
    user.is_active = False
    user.token_version += 1
    user.anonymized_at = datetime.now(timezone.utc)
    user.password_hash = hash_password(generate_api_key())
    for key in (await session.scalars(select(ApiKey).where(ApiKey.user_id == user.id))).all():
        key.revoked = True
    await audit.record(session, admin, "user.anonymize", target_type="user", target_id=user.id)
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
    await session.flush()
    await audit.record(session, user, "apikey.create", target_type="api_key", target_id=row.id,
                       details={"name": row.name, "prefix": row.prefix})
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
    await audit.record(session, user, "apikey.revoke", target_type="api_key", target_id=row.id)
    await session.commit()
