"""Append-only audit trail + security event logging.

Every state change goes through `record()` with the acting user, the channel (api / mcp / system) and a
compact `details` dict. Rows in `audit_events` are never updated or deleted by the application; the
same event is also emitted on the `usecasehub.audit` logger as one JSON line so a SIEM can ingest it
without touching the database.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AuditEvent, User

log = logging.getLogger("usecasehub.audit")

# Set per request by middleware (REST) or per call (MCP); read by record().
request_context: ContextVar[dict[str, str]] = ContextVar("audit_request_context", default={})


def set_request_context(*, request_id: str = "", client_ip: str = "", channel: str = "api") -> None:
    request_context.set({"request_id": request_id, "client_ip": client_ip, "channel": channel})


def _safe(value: Any) -> Any:
    """Keep details small and free of secrets/free text: scalars, short strings, small lists."""
    if isinstance(value, str):
        return value[:300]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in list(value)[:20]]
    if isinstance(value, dict):
        return {str(k)[:60]: _safe(v) for k, v in list(value.items())[:30]}
    return str(value)[:300]


async def record(session: AsyncSession, actor: User | None, action: str, *, target_type: str = "",
                 target_id: str = "", details: dict | None = None, outcome: str = "success",
                 channel: str | None = None) -> AuditEvent:
    ctx = request_context.get()
    ev = AuditEvent(
        actor_id=actor.id if actor else None,
        actor_email=(actor.email if actor else "")[:255],
        channel=channel or ctx.get("channel", "system"),
        action=action[:60],
        target_type=target_type[:40],
        target_id=str(target_id)[:64],
        details=_safe(details or {}),
        client_ip=ctx.get("client_ip", "")[:64],
        request_id=ctx.get("request_id", "")[:32],
        outcome=outcome[:20],
    )
    session.add(ev)
    await session.flush()
    log.info(json.dumps({
        "event": ev.action, "outcome": ev.outcome, "actor": ev.actor_email or ev.actor_id, "channel": ev.channel,
        "target": f"{ev.target_type}:{ev.target_id}" if ev.target_type else "", "request_id": ev.request_id,
        "ip": ev.client_ip, "details": ev.details,
    }, ensure_ascii=False, default=str))
    return ev
