from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import (can_edit, check_ai_quota, get_current_user, is_reviewer, require_owner_or_reviewer,
                    require_role)
from ..config import get_settings
from ..db import get_session
from ..models import (Decision, Relation, RelationStatus, RelationType, RiskValidation, Role, Status, Template,
                      UseCase, UseCaseEntity, UseCaseVersion, User)
from ..schemas import (DecisionOut, EntityOut, ImproveIn, RelationCreate, RelationOut, RelationReview,
                       RiskValidationIn, RiskValidationOut, TransitionIn, UseCaseCreate, UseCaseListItem,
                       UseCaseOut, UseCaseUpdate, VersionOut)
from ..services import analysis, audit, llm, normalizer, usecases as svc, workflow
from ..services.templates import get_default_template

router = APIRouter(prefix="/usecases", tags=["usecases"])

SORTABLE = {"updated_at", "created_at", "title", "ref", "status", "completeness", "value_score",
            "feasibility_score", "priority_score", "business_unit", "risk_tier"}
MAX_LIMIT = 1000


async def _suggested_counts(session: AsyncSession, ids: list[str]) -> dict[str, int]:
    if not ids:
        return {}
    rows = await session.execute(
        select(Relation.source_id, Relation.target_id)
        .where(Relation.status == RelationStatus.suggested.value,
               or_(Relation.source_id.in_(ids), Relation.target_id.in_(ids)))
    )
    counts: dict[str, int] = {}
    for s, t in rows.all():
        for x in (s, t):
            if x in ids:
                counts[x] = counts.get(x, 0) + 1
    return counts


def _to_item(uc: UseCase, suggested: int = 0, model=UseCaseListItem, role: str | None = None):
    data = {c: getattr(uc, c) for c in model.model_fields if hasattr(uc, c)}
    data["ai_capability"] = (uc.fields or {}).get("ai_capability")
    data["suggested_relations"] = suggested
    if model is UseCaseOut and role:
        data["allowed_transitions"] = workflow.allowed_transitions(uc.status, role)
    return model.model_validate(data)


async def _get_uc(session: AsyncSession, uc_id: str) -> UseCase:
    uc = await session.get(UseCase, uc_id)
    if not uc:
        uc = await session.scalar(select(UseCase).where(UseCase.ref == uc_id.upper()))
    if not uc or uc.deleted_at:
        raise HTTPException(404, "Use case not found")
    return uc


async def _out(session: AsyncSession, uc: UseCase, user: User) -> UseCaseOut:
    counts = await _suggested_counts(session, [uc.id])
    return _to_item(uc, counts.get(uc.id, 0), UseCaseOut, user.role)


@router.get("", response_model=list[UseCaseListItem])
async def list_use_cases(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(get_current_user),
    q: str | None = Query(None, max_length=200),
    status: list[str] | None = Query(None),
    business_unit: str | None = Query(None, max_length=255),
    risk_tier: str | None = Query(None, max_length=40),
    capability: str | None = Query(None, max_length=60),
    sort: str = "updated_at",
    order: str = "desc",
    limit: int = Query(500, ge=1, le=MAX_LIMIT),
):
    if sort not in SORTABLE:
        raise HTTPException(400, f"sort must be one of {sorted(SORTABLE)}")
    stmt = select(UseCase).where(UseCase.deleted_at.is_(None))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(UseCase.title.ilike(like), UseCase.summary.ilike(like), UseCase.ref.ilike(like)))
    if status:
        stmt = stmt.where(UseCase.status.in_(status))
    if business_unit:
        stmt = stmt.where(UseCase.business_unit == business_unit)
    if risk_tier:
        stmt = stmt.where(UseCase.risk_tier == risk_tier)
    col = getattr(UseCase, sort)
    stmt = stmt.order_by(col.desc().nulls_last() if order == "desc" else col.asc().nulls_last()).limit(limit)
    ucs = (await session.scalars(stmt)).all()
    if capability:
        ucs = [u for u in ucs if (u.fields or {}).get("ai_capability") == capability]
    counts = await _suggested_counts(session, [u.id for u in ucs])
    return [_to_item(u, counts.get(u.id, 0)) for u in ucs]


@router.post("", response_model=UseCaseOut, status_code=201)
async def create_use_case(payload: UseCaseCreate, bg: BackgroundTasks,
                          session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    template = await session.get(Template, payload.template_id) if payload.template_id else None
    template = template or await get_default_template(session)
    uc = await svc.create_use_case(session, template, payload.fields, user, tags=payload.tags)
    await audit.record(session, user, "usecase.create", target_type="usecase", target_id=uc.ref,
                       details={"template": template.key, "source": "manual"})
    await session.commit()
    if get_settings().auto_analyze_on_change:
        bg.add_task(svc.analyze_in_background, uc.id)
    return await _out(session, uc, user)


@router.get("/{uc_id}", response_model=UseCaseOut)
async def get_use_case(uc_id: str, session: AsyncSession = Depends(get_session),
                       user: User = Depends(get_current_user)):
    return await _out(session, await _get_uc(session, uc_id), user)


@router.put("/{uc_id}", response_model=UseCaseOut)
async def update_use_case(uc_id: str, payload: UseCaseUpdate, bg: BackgroundTasks,
                          session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    require_owner_or_reviewer(user, uc.owner_id)
    if uc.status in (Status.in_production.value, Status.retired.value) and not is_reviewer(user):
        raise HTTPException(403, "Use cases in production can only be edited by reviewers")
    was_stale = uc.risk_stale
    await svc.update_fields(session, uc, payload.fields, user, payload.change_note)
    if payload.tags is not None:
        uc.tags = [str(t)[:50] for t in payload.tags][:20]
    details = {"note": payload.change_note, "risk_became_stale": bool(uc.risk_stale and not was_stale)}
    await audit.record(session, user, "usecase.update", target_type="usecase", target_id=uc.ref, details=details)
    await session.commit()
    if get_settings().auto_analyze_on_change:
        bg.add_task(svc.analyze_in_background, uc.id)
    return await _out(session, uc, user)


@router.delete("/{uc_id}", status_code=204)
async def delete_use_case(uc_id: str, session: AsyncSession = Depends(get_session),
                          user: User = Depends(require_role(Role.admin))):
    """Soft delete: the record, its versions, decisions and audit trail are retained."""
    uc = await _get_uc(session, uc_id)
    svc.soft_delete(uc, user)
    await audit.record(session, user, "usecase.delete", target_type="usecase", target_id=uc.ref)
    await session.commit()


# ---------------------------------------------------------------- workflow

@router.post("/{uc_id}/transition", response_model=UseCaseOut)
async def transition(uc_id: str, payload: TransitionIn, session: AsyncSession = Depends(get_session),
                     user: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    ok, reason = workflow.can_transition(uc.status, payload.to, user.role)
    if not ok:
        raise HTTPException(400, reason)
    if not is_reviewer(user) and uc.owner_id != user.id:
        raise HTTPException(403, "Only the owner can move this use case")
    if payload.to == Status.submitted.value:
        if uc.missing_required:
            raise HTTPException(400, f"Required fields missing: {', '.join(uc.missing_required)}")
        min_c = float(uc.template.definition.get("submission_min_completeness", 0))
        if uc.completeness < min_c:
            raise HTTPException(400, f"Completeness {uc.completeness:.0%} is below the {min_c:.0%} required to submit")
    if payload.to == Status.approved.value:
        if not uc.risk_tier:
            raise HTTPException(400, "A reviewer must validate the EU AI Act risk tier before approval")
        if uc.risk_stale:
            raise HTTPException(400, "Risk-relevant fields changed since the last validation; re-validate the tier")
        if uc.risk_tier == "prohibited":
            raise HTTPException(400, "A use case classified as prohibited cannot be approved")
    session.add(Decision(use_case_id=uc.id, from_status=uc.status, to_status=payload.to,
                         comment=payload.comment, decided_by_id=user.id))
    await audit.record(session, user, "usecase.transition", target_type="usecase", target_id=uc.ref,
                       details={"from": uc.status, "to": payload.to, "comment": payload.comment})
    uc.status = payload.to
    await session.commit()
    return await _out(session, uc, user)


@router.get("/{uc_id}/decisions", response_model=list[DecisionOut])
async def decisions(uc_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    return (await session.scalars(select(Decision).where(Decision.use_case_id == uc.id)
                                  .order_by(Decision.created_at.desc()))).all()


@router.get("/{uc_id}/versions", response_model=list[VersionOut])
async def versions(uc_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    require_owner_or_reviewer(user, uc.owner_id, "the version history")
    return (await session.scalars(select(UseCaseVersion).where(UseCaseVersion.use_case_id == uc.id)
                                  .order_by(UseCaseVersion.version.desc()))).all()


# ---------------------------------------------------------------- relations & entities

def _relation_out(r: Relation, by_id: dict[str, UseCase]) -> RelationOut:
    out = RelationOut.model_validate(r)
    s, t = by_id.get(r.source_id), by_id.get(r.target_id)
    out.source_ref, out.source_title = (s.ref, s.title) if s else (None, None)
    out.target_ref, out.target_title = (t.ref, t.title) if t else (None, None)
    return out


@router.get("/{uc_id}/relations", response_model=list[RelationOut])
async def relations(uc_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    rels = (await session.scalars(select(Relation).where(or_(Relation.source_id == uc.id, Relation.target_id == uc.id))
                                  .order_by(Relation.status, Relation.confidence.desc()))).all()
    ids = {r.source_id for r in rels} | {r.target_id for r in rels}
    by_id = {u.id: u for u in (await session.scalars(select(UseCase).where(UseCase.id.in_(ids)))).all()} if ids else {}
    return [_relation_out(r, by_id) for r in rels]


@router.post("/{uc_id}/relations", response_model=RelationOut, status_code=201)
async def add_relation(uc_id: str, payload: RelationCreate, session: AsyncSession = Depends(get_session),
                       user: User = Depends(require_role(Role.reviewer))):
    uc = await _get_uc(session, uc_id)
    target = await _get_uc(session, payload.target_id)
    if uc.id == target.id:
        raise HTTPException(400, "A use case cannot relate to itself")
    if payload.type not in {t.value for t in RelationType} or payload.type == RelationType.shares_entity.value:
        raise HTTPException(400, "Invalid relation type")
    now = datetime.now(timezone.utc)
    r = Relation(source_id=uc.id, target_id=target.id, type=payload.type, status=RelationStatus.confirmed.value,
                 confidence=1.0, explanation=payload.explanation[:2000], origin="human", reviewed_by_id=user.id,
                 reviewed_at=now, review_history=[{"status": "confirmed", "type": payload.type, "by": user.id,
                                                   "at": now.isoformat()}])
    session.add(r)
    await session.flush()
    await audit.record(session, user, "relation.add", target_type="relation", target_id=r.id,
                       details={"source": uc.ref, "target": target.ref, "type": payload.type})
    await session.commit()
    return _relation_out(r, {uc.id: uc, target.id: target})


@router.patch("/relations/{relation_id}", response_model=RelationOut)
async def review_relation(relation_id: str, payload: RelationReview, session: AsyncSession = Depends(get_session),
                          user: User = Depends(require_role(Role.reviewer))):
    r = await session.get(Relation, relation_id)
    if not r:
        raise HTTPException(404, "Relation not found")
    if payload.status not in (RelationStatus.confirmed.value, RelationStatus.dismissed.value):
        raise HTTPException(400, "status must be confirmed or dismissed")
    if payload.type:
        if payload.type not in {t.value for t in RelationType}:
            raise HTTPException(400, "Invalid relation type")
        r.type = payload.type
    now = datetime.now(timezone.utc)
    previous = r.status
    r.status = payload.status
    r.reviewed_by_id = user.id
    r.reviewed_at = now
    r.review_history = [*(r.review_history or []),
                        {"status": payload.status, "type": r.type, "by": user.id, "at": now.isoformat()}]
    await audit.record(session, user, "relation.review", target_type="relation", target_id=r.id,
                       details={"from": previous, "to": payload.status, "type": r.type})
    await session.commit()
    ucs = (await session.scalars(select(UseCase).where(UseCase.id.in_([r.source_id, r.target_id])))).all()
    return _relation_out(r, {u.id: u for u in ucs})


@router.get("/{uc_id}/entities")
async def entities(uc_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    links = (await session.scalars(select(UseCaseEntity).where(UseCaseEntity.use_case_id == uc.id))).all()
    if not links:
        return []
    shared = dict((await session.execute(
        select(UseCaseEntity.entity_id, func.count(func.distinct(UseCaseEntity.use_case_id)))
        .where(UseCaseEntity.entity_id.in_([l.entity_id for l in links]), UseCaseEntity.use_case_id != uc.id)
        .group_by(UseCaseEntity.entity_id))).all())
    return [{"entity": EntityOut.model_validate(l.entity), "field_id": l.field_id, "role": l.role,
             "shared_with": shared.get(l.entity_id, 0)} for l in links]


# ---------------------------------------------------------------- AI actions

@router.post("/{uc_id}/ai/improve")
async def ai_improve(uc_id: str, payload: ImproveIn, session: AsyncSession = Depends(get_session),
                     user: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    require_owner_or_reviewer(user, uc.owner_id)
    check_ai_quota(user)
    value = payload.value if payload.value is not None else uc.fields.get(payload.field_id)
    try:
        result = await normalizer.improve_field(uc.template.definition, payload.field_id, value, uc.fields,
                                               payload.instruction)
    except llm.LLMUnavailable as e:
        raise HTTPException(503, llm.public_message(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    await audit.record(session, user, "ai.improve_field", target_type="usecase", target_id=uc.ref,
                       details={"field": payload.field_id})
    await session.commit()
    return result


@router.post("/{uc_id}/ai/risk")
async def ai_risk(uc_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    require_owner_or_reviewer(user, uc.owner_id)
    check_ai_quota(user)
    try:
        suggestion = await normalizer.suggest_risk(uc.template.definition, {**uc.fields, "title": uc.title,
                                                                           "summary": uc.summary})
    except llm.LLMUnavailable as e:
        raise HTTPException(503, llm.public_message(e))
    uc.ai_suggestions = {**(uc.ai_suggestions or {}), **suggestion}
    await audit.record(session, user, "ai.risk_suggestion", target_type="usecase", target_id=uc.ref,
                       details={"tier": suggestion.get("risk_tier"), "model": suggestion.get("model")})
    await session.commit()
    return suggestion


@router.post("/{uc_id}/risk/validate", response_model=UseCaseOut)
async def validate_risk(uc_id: str, body: RiskValidationIn, session: AsyncSession = Depends(get_session),
                        user: User = Depends(require_role(Role.reviewer))):
    uc = await _get_uc(session, uc_id)
    options = {f["id"]: f for s in uc.template.definition["sections"] for f in s["fields"]}.get(
        "eu_ai_act_tier", {}).get("options", [])
    if body.risk_tier not in options:
        raise HTTPException(400, f"risk_tier must be one of {options}")
    try:
        rv = await svc.validate_risk(session, uc, user, body.risk_tier, body.rationale,
                                     ai_suggestion={k: v for k, v in (uc.ai_suggestions or {}).items()
                                                    if k.startswith("risk") or k in ("mitigations", "confidence", "model")})
    except PermissionError as e:
        raise HTTPException(403, str(e))
    await audit.record(session, user, "risk.validate", target_type="usecase", target_id=uc.ref,
                       details={"tier": body.risk_tier, "validation_id": rv.id,
                                "ai_suggested": (uc.ai_suggestions or {}).get("risk_tier")})
    await session.commit()
    return await _out(session, uc, user)


@router.get("/{uc_id}/risk/validations", response_model=list[RiskValidationOut])
async def risk_validations(uc_id: str, session: AsyncSession = Depends(get_session),
                           _: User = Depends(get_current_user)):
    uc = await _get_uc(session, uc_id)
    return (await session.scalars(select(RiskValidation).where(RiskValidation.use_case_id == uc.id)
                                  .order_by(RiskValidation.created_at.desc()))).all()


@router.post("/{uc_id}/analyze")
async def analyze(uc_id: str, session: AsyncSession = Depends(get_session), user: User = Depends(get_current_user),
                  use_llm: bool = True):
    uc = await _get_uc(session, uc_id)
    require_owner_or_reviewer(user, uc.owner_id)
    if use_llm:
        check_ai_quota(user)
    try:
        stats = await analysis.analyze_use_case(session, uc, use_llm=use_llm)
    except llm.LLMUnavailable as e:
        raise HTTPException(503, llm.public_message(e))
    await audit.record(session, user, "analysis.usecase", target_type="usecase", target_id=uc.ref, details=stats)
    await session.commit()
    return stats
