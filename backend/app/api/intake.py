from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user
from ..config import get_settings
from ..db import get_session
from ..models import IntakeSession, Status, Template, User
from ..schemas import IntakeDraftUpdate, IntakeMessage, IntakeOut, IntakeStart, NormalizeIn
from ..services import intake_agent, llm, normalizer, usecases as svc
from ..services.templates import completeness, get_default_template, normalize_fields

router = APIRouter(prefix="/intake", tags=["intake"])


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _own(session: AsyncSession, intake_id: str, user: User) -> IntakeSession:
    it = await session.get(IntakeSession, intake_id)
    if not it or (it.user_id != user.id and user.role == "requester"):
        raise HTTPException(404, "Intake session not found")
    return it


@router.get("", response_model=list[IntakeOut])
async def list_sessions(session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    stmt = select(IntakeSession).order_by(IntakeSession.updated_at.desc()).limit(50)
    if user.role == "requester":
        stmt = stmt.where(IntakeSession.user_id == user.id)
    return (await session.scalars(stmt)).all()


@router.post("", response_model=IntakeOut, status_code=201)
async def start(payload: IntakeStart, session: AsyncSession = Depends(get_session),
                user: User = Depends(get_current_user)):
    template = await session.get(Template, payload.template_id) if payload.template_id else None
    template = template or await get_default_template(session)
    it = IntakeSession(user_id=user.id, template_id=template.id, draft={}, assumptions=[],
                       messages=[{"role": "assistant", "content": intake_agent.opening_message(template.definition),
                                  "ts": _ts()}])
    session.add(it)
    await session.flush()
    if payload.initial_text:
        await _turn(it, template, payload.initial_text)
    await session.commit()
    return it


@router.get("/{intake_id}", response_model=IntakeOut)
async def get_session_(intake_id: str, session: AsyncSession = Depends(get_session),
                       user: User = Depends(get_current_user)):
    return await _own(session, intake_id, user)


async def _turn(it: IntakeSession, template: Template, content: str) -> None:
    it.messages = [*it.messages, {"role": "user", "content": content, "ts": _ts()}]
    try:
        state = await intake_agent.run_turn(template.definition, it.messages, it.draft, it.assumptions)
    except llm.LLMUnavailable as e:
        raise HTTPException(503, str(e))
    it.draft = state.get("draft") or it.draft
    it.assumptions = state.get("assumptions") or it.assumptions
    it.completeness = float(state.get("completeness") or 0)
    it.missing_required = state.get("missing_required") or []
    it.phase = state.get("phase") or "interview"
    it.messages = [*it.messages, {"role": "assistant", "content": state.get("reply", ""), "ts": _ts()}]


@router.post("/{intake_id}/messages", response_model=IntakeOut)
async def send_message(intake_id: str, payload: IntakeMessage, session: AsyncSession = Depends(get_session),
                       user: User = Depends(get_current_user)):
    it = await _own(session, intake_id, user)
    if it.phase == "converted":
        raise HTTPException(400, "This intake was already converted into a use case")
    template = await session.get(Template, it.template_id)
    await _turn(it, template, payload.content.strip())
    await session.commit()
    return it


@router.put("/{intake_id}/draft", response_model=IntakeOut)
async def edit_draft(intake_id: str, payload: IntakeDraftUpdate, session: AsyncSession = Depends(get_session),
                     user: User = Depends(get_current_user)):
    """Manual edits from the live canvas (the user can always override the AI)."""
    it = await _own(session, intake_id, user)
    template = await session.get(Template, it.template_id)
    it.draft = normalize_fields(template.definition, {**it.draft, **payload.draft})
    for k, v in payload.draft.items():  # explicit clears
        if v in (None, "", []):
            it.draft.pop(k, None)
    it.completeness, it.missing_required = completeness(template.definition, it.draft)
    if not it.missing_required and it.completeness >= intake_agent.READY_COMPLETENESS:
        it.phase = "ready" if it.phase != "converted" else it.phase
    await session.commit()
    return it


@router.post("/{intake_id}/convert", response_model=IntakeOut)
async def convert(intake_id: str, bg: BackgroundTasks, session: AsyncSession = Depends(get_session),
                  user: User = Depends(get_current_user), submit: bool = False):
    it = await _own(session, intake_id, user)
    if it.phase == "converted":
        raise HTTPException(400, "Already converted")
    template = await session.get(Template, it.template_id)
    _, missing = completeness(template.definition, it.draft)
    if submit and missing:
        raise HTTPException(400, f"Required fields missing: {', '.join(missing)}")
    uc = await svc.create_use_case(
        session, template, it.draft, user,
        status=Status.submitted.value if submit else Status.draft.value, source="intake",
        ai_suggestions={"assumptions": it.assumptions},
    )
    it.use_case_id = uc.id
    it.phase = "converted"
    await session.commit()
    if get_settings().auto_analyze_on_change:
        bg.add_task(svc.analyze_in_background, uc.id)
    return it


@router.post("/normalize")
async def normalize(payload: NormalizeIn, session: AsyncSession = Depends(get_session),
                    _: User = Depends(get_current_user)):
    """One-shot: paste a brief / email / slide text and get a normalized draft back."""
    template = await session.get(Template, payload.template_id) if payload.template_id else None
    template = template or await get_default_template(session)
    try:
        result = await normalizer.normalize_text(template.definition, payload.text, payload.existing)
    except llm.LLMUnavailable as e:
        raise HTTPException(503, str(e))
    score, missing = completeness(template.definition, result["fields"])
    return {**result, "completeness": score, "missing_required": missing, "template_id": template.id}
