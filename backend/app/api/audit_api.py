"""Read-only access to the audit trail (reviewers/admins). There is deliberately no write or delete endpoint."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_role
from ..db import get_session
from ..models import AuditEvent, Role, User
from ..schemas import AuditEventOut

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=list[AuditEventOut])
async def list_events(session: AsyncSession = Depends(get_session), _: User = Depends(require_role(Role.reviewer)),
                      action: str | None = Query(None, max_length=60), target_id: str | None = Query(None, max_length=64),
                      actor_id: str | None = Query(None, max_length=32), limit: int = Query(200, ge=1, le=1000)):
    stmt = select(AuditEvent).order_by(AuditEvent.ts.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditEvent.action == action)
    if target_id:
        stmt = stmt.where(AuditEvent.target_id == target_id)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    return (await session.scalars(stmt)).all()
