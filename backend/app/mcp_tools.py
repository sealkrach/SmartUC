"""MCP tool implementations: plain async functions reusing the REST endpoint logic.

Each function opens its own DB session, calls the same code the HTTP API runs (so permissions, gates and
validation are identical), and returns JSON-serialisable data. ``HTTPException`` is translated into a
``ToolError``-style ValueError so the MCP client gets a readable message.
"""
from __future__ import annotations

from typing import Any

from fastapi import BackgroundTasks, HTTPException
from mcp.server.mcpserver.exceptions import ToolError as SdkToolError
from pydantic import BaseModel
from sqlalchemy import select

from .api import intake as intake_api, portfolio as portfolio_api, templates as templates_api
from .api import usecases as uc_api
from .db import SessionLocal
from .models import Template, UseCase, User
from .schemas import (ImproveIn, IntakeMessage, IntakeStart, NormalizeIn, RelationCreate, RelationReview,
                      TransitionIn, UseCaseCreate, UseCaseUpdate)
from .services import analysis, llm
from .services.templates import get_default_template, schema_for_llm


class ToolError(SdkToolError, ValueError):
    """Anticipated failure: the MCP client receives the message (is_error=True) instead of a generic crash."""


def _out(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_out(o) for o in obj]
    return obj


async def _run(user: User | None, coro_factory):
    """Run an endpoint coroutine with a fresh session, translating HTTP errors.

    The acting user is re-attached to the session so relationship access (uc.owner) hits the identity map
    instead of triggering a lazy load, which is not allowed in async SQLAlchemy.
    """
    async with SessionLocal() as session:
        bg = BackgroundTasks()
        if user is not None and user.id != "mcp":
            user = await session.get(User, user.id) or user
        try:
            result = await coro_factory(session, bg, user)
        except HTTPException as e:
            raise ToolError(f"{e.status_code}: {e.detail}") from e
        except llm.LLMUnavailable as e:
            raise ToolError(f"503: {e}") from e
        # run what the API would have scheduled in the background (analysis), synchronously
        for task in bg.tasks:
            await task()
        return _out(result)


async def _template(session, key: str | None) -> Template:
    if key:
        t = await session.scalar(select(Template).where(Template.key == key).order_by(Template.version.desc()))
        if not t:
            raise ToolError(f"404: template '{key}' not found")
        return t
    return await get_default_template(session)


def _anon() -> User:
    """Resources are gated at the transport level; use a read-only reviewer identity for serialisation."""
    return User(id="mcp", email="mcp@local", role="reviewer", password_hash="", is_active=True)


# ---------------------------------------------------------------- registry

async def list_use_cases(user: User, *, q=None, status=None, business_unit=None, risk_tier=None, limit=50):
    rows = await _run(user, lambda s, bg, user: uc_api.list_use_cases(session=s, _=user, q=q, status=status,
                                                          business_unit=business_unit, risk_tier=risk_tier,
                                                          capability=None, sort="updated_at", order="desc", limit=limit))
    keep = ("id", "ref", "title", "status", "business_unit", "risk_tier", "completeness", "value_score",
            "feasibility_score", "priority_score", "ai_capability", "suggested_relations", "updated_at")
    return [{k: r[k] for k in keep} for r in rows]


async def get_use_case(user: User | None, ref: str):
    user = user or _anon()
    return await _run(user, lambda s, bg, user: uc_api.get_use_case(ref, session=s, user=user))


async def search_similar(user: User, text: str, limit: int = 5):
    async with SessionLocal() as session:
        [vec] = await llm.embed([text])
        ucs = (await session.scalars(select(UseCase).where(UseCase.embedding.is_not(None)))).all()
        scored = sorted(((llm.cosine(vec, u.embedding), u) for u in ucs), key=lambda x: -x[0])[:limit]
        return [{"ref": u.ref, "id": u.id, "title": u.title, "status": u.status, "business_unit": u.business_unit,
                 "similarity": round(sim, 3), "summary": u.summary} for sim, u in scored]


async def create_use_case(user: User, fields: dict, *, submit: bool = False, template_key: str | None = None):
    async def go(s, bg, user):
        t = await _template(s, template_key)
        uc = await uc_api.create_use_case(UseCaseCreate(fields=fields, template_id=t.id), bg, session=s, user=user)
        if submit:
            uc = await uc_api.transition(uc.id, TransitionIn(to="submitted", comment="Submitted via MCP"),
                                         session=s, user=user)
        return uc
    return await _run(user, go)


async def update_use_case(user: User, ref: str, fields: dict, *, note: str = "Updated via MCP"):
    async def go(s, bg, user):
        current = await uc_api.get_use_case(ref, session=s, user=user)
        merged = {**current.fields, **fields}
        for k, v in fields.items():  # explicit clears
            if v in (None, "", []):
                merged.pop(k, None)
        return await uc_api.update_use_case(current.id, UseCaseUpdate(fields=merged, change_note=note), bg,
                                            session=s, user=user)
    return await _run(user, go)


async def transition_use_case(user: User, ref: str, to: str, comment: str = ""):
    return await _run(user, lambda s, bg, user: uc_api.transition(ref, TransitionIn(to=to, comment=comment), session=s, user=user))


async def get_history(user: User, ref: str):
    async def go(s, bg, user):
        return {"decisions": await uc_api.decisions(ref, session=s, _=user),
                "versions": [{"version": v.version, "change_note": v.change_note, "created_at": v.created_at}
                             for v in await uc_api.versions(ref, session=s, _=user)]}
    return await _run(user, go)


# ---------------------------------------------------------------- AI assistance

async def normalize_text(user: User, text: str, *, template_key: str | None = None):
    async def go(s, bg, user):
        t = await _template(s, template_key)
        return await intake_api.normalize(NormalizeIn(text=text, template_id=t.id), session=s, _=user)
    return await _run(user, go)


async def improve_field(user: User, ref: str, field_id: str, instruction: str = ""):
    return await _run(user, lambda s, bg, user: uc_api.ai_improve(ref, ImproveIn(field_id=field_id, instruction=instruction),
                                                      session=s, _=user))


async def suggest_risk(user: User, ref: str):
    return await _run(user, lambda s, bg, user: uc_api.ai_risk(ref, session=s, _=user))


async def validate_risk(user: User, ref: str, risk_tier: str, rationale: str = ""):
    if user.role not in ("reviewer", "admin"):
        raise ToolError("403: validating a risk tier requires the reviewer role")
    return await _run(user, lambda s, bg, user: uc_api.validate_risk(ref, {"risk_tier": risk_tier, "rationale": rationale},
                                                         session=s, user=user))


# ---------------------------------------------------------------- intake

def _intake_view(it) -> dict:
    d = _out(it) if isinstance(it, BaseModel) else {
        "id": it.id, "phase": it.phase, "draft": it.draft, "completeness": it.completeness,
        "missing_required": it.missing_required, "assumptions": it.assumptions, "use_case_id": it.use_case_id,
        "messages": it.messages}
    last = next((m["content"] for m in reversed(d.get("messages", [])) if m["role"] == "assistant"), "")
    d = {k: v for k, v in d.items() if k != "messages"}
    d["assistant_reply"] = last
    return d


async def start_intake(user: User, initial_text: str | None, *, template_key: str | None = None):
    async def go(s, bg, user):
        t = await _template(s, template_key)
        return _intake_view(await intake_api.start(IntakeStart(template_id=t.id, initial_text=initial_text),
                                                   session=s, user=user))
    return await _run(user, go)


async def continue_intake(user: User, intake_id: str, message: str):
    return await _run(user, lambda s, bg, user: _intake_view_async(intake_api.send_message(intake_id, IntakeMessage(content=message),
                                                                               session=s, user=user)))


async def _intake_view_async(coro):
    return _intake_view(await coro)


async def convert_intake(user: User, intake_id: str, submit: bool = False):
    return await _run(user, lambda s, bg, user: _intake_view_async(intake_api.convert(intake_id, bg, session=s, user=user, submit=submit)))


# ---------------------------------------------------------------- portfolio intelligence

async def list_relations(user: User, *, ref=None, status="suggested", include_structural=False, limit=100):
    async def go(s, bg, user):
        if ref:
            rels = await uc_api.relations(ref, session=s, _=user)
            if status != "all":
                rels = [r for r in rels if r.status == status]
        else:
            rels = await portfolio_api.relation_inbox(session=s, _=user, status=status, limit=limit)
        if not include_structural:
            rels = [r for r in rels if r.type != "shares_entity"]
        return [{k: v for k, v in _out(r).items() if k not in ("entity",) or v} for r in rels[:limit]]
    return await _run(user, go)


async def review_relation(user: User, relation_id: str, status: str, type: str | None = None):
    return await _run(user, lambda s, bg, user: uc_api.review_relation(relation_id, RelationReview(status=status, type=type),
                                                           session=s, user=user))


async def add_relation(user: User, source_ref: str, target_ref: str, type: str, explanation: str = ""):
    async def go(s, bg, user):
        target = await uc_api.get_use_case(target_ref, session=s, user=user)
        return await uc_api.add_relation(source_ref, RelationCreate(target_id=target.id, type=type,
                                                                    explanation=explanation), session=s, user=user)
    return await _run(user, go)


async def analyze_use_case(user: User, ref: str, use_llm: bool = True):
    return await _run(user, lambda s, bg, user: uc_api.analyze(ref, session=s, _=user, use_llm=use_llm))


async def analyze_portfolio(user: User, use_llm: bool = True):
    if user.role not in ("reviewer", "admin"):
        raise ToolError("403: portfolio analysis requires the reviewer role")
    async with SessionLocal() as session:
        return await analysis.analyze_portfolio(session, use_llm=use_llm)


async def dashboard(user: User):
    return await _run(user, lambda s, bg, user: portfolio_api.dashboard(session=s, _=user))


async def list_entities(user: User, kind: str | None = None):
    return await _run(user, lambda s, bg, user: portfolio_api.list_entities(session=s, _=user, kind=kind))


# ---------------------------------------------------------------- templates

async def list_templates(user: User):
    rows = await _run(user, lambda s, bg, user: templates_api.list_templates(session=s, _=user))
    return [{k: r[k] for k in ("id", "key", "version", "name", "is_default")} for r in rows]


async def get_template(user: User | None, key: str | None = None):
    async with SessionLocal() as session:
        t = await _template(session, key)
        return {"id": t.id, "key": t.key, "version": t.version, "name": t.name,
                "fields_schema": schema_for_llm(t.definition),
                "sections": t.definition["sections"],
                "submission_min_completeness": t.definition.get("submission_min_completeness", 0)}

