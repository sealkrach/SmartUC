"""Demo mode: load / remove the sample portfolio from the UI (admin)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Request
from pydantic import BaseModel

from ..auth import client_ip, create_guest_token, get_current_user, login_guard, require_role
from ..db import get_session
from ..models import AppSetting, Role, UseCase, User
from ..schemas import Token
from ..services import audit
from ..seed import DEMO, DEMO_TAG, ensure_demo_guest, load_demo, remove_demo

router = APIRouter(prefix="/demo", tags=["demo"])
KEY = "demo"


async def _status(session: AsyncSession) -> dict:
    row = await session.get(AppSetting, KEY)
    ucs = (await session.scalars(select(UseCase.tags).where(UseCase.deleted_at.is_(None)))).all()
    count = sum(1 for t in ucs if DEMO_TAG in (t or []))
    return {"active": count > 0, "count": count, "available": len(DEMO), **(row.value if row else {})}


@router.get("")
async def status(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return await _status(session)


@router.get("/public")
async def public_status(session: AsyncSession = Depends(get_session)):
    """Unauthenticated: tells the login page whether a guest demo is available."""
    st = await _status(session)
    return {"enabled": bool(st.get("public")) and st["active"], "count": st["count"] if st["active"] else 0}


class PublicToggle(BaseModel):
    enabled: bool


@router.put("/public")
async def set_public(payload: PublicToggle, session: AsyncSession = Depends(get_session),
                     admin: User = Depends(require_role(Role.admin))):
    row = await session.get(AppSetting, KEY)
    value = {**(row.value if row else {}), "public": payload.enabled}
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=KEY, value=value))
    if payload.enabled:
        await ensure_demo_guest(session)
    else:  # kill every guest session
        guest = await session.scalar(select(User).where(User.is_demo_guest.is_(True)))
        if guest:
            guest.token_version += 1
    await audit.record(session, admin, "demo.public", target_type="demo", details={"enabled": payload.enabled})
    await session.commit()
    return await _status(session)


@router.post("/guest-login", response_model=Token)
async def guest_login(request: Request, session: AsyncSession = Depends(get_session)):
    """Unauthenticated: issues a 2-hour read-only token when the public demo is enabled."""
    ip = client_ip(request)
    if login_guard.is_locked(f"ip:{ip}"):
        raise HTTPException(429, "Too many attempts; try again later")
    st = await _status(session)
    if not (st.get("public") and st["active"]):
        login_guard.register_failure(f"ip:{ip}")
        raise HTTPException(404, "The public demo is not available")
    guest = await ensure_demo_guest(session)
    await audit.record(session, guest, "auth.demo_login", target_type="user", target_id=guest.id)
    await session.commit()
    return Token(access_token=create_guest_token(guest))


@router.post("/load")
async def load(session: AsyncSession = Depends(get_session), admin: User = Depends(require_role(Role.admin))):
    if (await _status(session))["active"]:
        raise HTTPException(409, "Demo data is already loaded")
    n = await load_demo(session, admin)
    row = await session.get(AppSetting, KEY)
    value = {"loaded_at": datetime.now(timezone.utc).isoformat(), "loaded_by": admin.email}
    if row:
        row.value = value
    else:
        session.add(AppSetting(key=KEY, value=value))
    await audit.record(session, admin, "demo.load", target_type="demo", details={"count": n})
    await session.commit()
    return await _status(session)


@router.post("/remove")
async def remove(session: AsyncSession = Depends(get_session), admin: User = Depends(require_role(Role.admin))):
    n = await remove_demo(session)
    row = await session.get(AppSetting, KEY)
    if row:
        await session.delete(row)
    await audit.record(session, admin, "demo.remove", target_type="demo", details={"count": n})
    await session.commit()
    return await _status(session)
