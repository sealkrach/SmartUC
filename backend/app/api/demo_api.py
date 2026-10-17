"""Demo mode: load / remove the sample portfolio from the UI (admin)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_role
from ..db import get_session
from ..models import AppSetting, Role, UseCase, User
from ..services import audit
from ..seed import DEMO, DEMO_TAG, load_demo, remove_demo

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
