from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import create_token, get_current_user, hash_password, require_role, verify_password
from ..db import get_session
from ..models import Role, User
from ..schemas import Token, UserCreate, UserOut, UserUpdate

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
