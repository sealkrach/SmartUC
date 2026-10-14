"""Use case lifecycle helpers shared by API, intake, import, MCP and seed."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import RiskValidation, Status, Template, UseCase, UseCaseVersion, User
from . import analysis
from .templates import completeness, compute_scores, normalize_fields

log = logging.getLogger(__name__)

DEFAULT_RISK_FIELDS = ["ai_capability", "solution_description", "human_in_the_loop", "target_users",
                       "data_sources", "data_sensitivity"]


async def next_ref(session: AsyncSession) -> str:
    """Next UC-NNNN from the highest existing number (soft-deleted rows included, so refs are never reused)."""
    refs = (await session.scalars(select(UseCase.ref))).all()
    highest = 0
    for r in refs:
        try:
            highest = max(highest, int(str(r).split("-")[-1]))
        except ValueError:
            continue
    return f"UC-{highest + 1:04d}"


def risk_fields_hash(template: Template, fields: dict) -> str:
    names = template.definition.get("risk_relevant_fields") or DEFAULT_RISK_FIELDS
    subset = {k: fields.get(k) for k in names}
    return hashlib.sha256(json.dumps(subset, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def apply_fields(uc: UseCase, template: Template, raw_fields: dict) -> None:
    """Apply user/LLM/import-provided fields. Governance fields (validated risk tier) are preserved from the
    current record, never taken from the input (F-01)."""
    definition = template.definition
    fields = normalize_fields(definition, raw_fields)
    # carry over governance values from the existing record (display only; source of truth = RiskValidation)
    if uc.risk_tier:
        fields["eu_ai_act_tier"] = uc.risk_tier
        if (uc.fields or {}).get("eu_ai_act_rationale"):
            fields["eu_ai_act_rationale"] = uc.fields["eu_ai_act_rationale"]
    uc.fields = fields
    uc.title = (fields.get("title") or uc.title or "Untitled use case")[:255]
    uc.summary = fields.get("summary") or uc.summary or ""
    uc.business_unit = (fields.get("business_unit") or uc.business_unit or "")[:255]
    uc.completeness, uc.missing_required = completeness(definition, fields)
    scores = compute_scores(definition, fields)
    uc.value_score = scores["value_score"]
    uc.feasibility_score = scores["feasibility_score"]
    uc.priority_score = scores["priority_score"]
    # stale-on-change: a validated tier no longer covers the use case once risk-relevant fields moved
    if uc.risk_tier and uc.risk_fields_hash and risk_fields_hash(template, fields) != uc.risk_fields_hash:
        uc.risk_stale = True


async def create_use_case(
    session: AsyncSession,
    template: Template,
    raw_fields: dict,
    owner: User | None,
    *,
    status: str = Status.draft.value,
    source: str = "manual",
    tags: list[str] | None = None,
    ai_suggestions: dict | None = None,
) -> UseCase:
    for attempt in range(5):  # ref uniqueness under concurrency
        uc = UseCase(
            ref=await next_ref(session),
            template_id=template.id,
            owner_id=owner.id if owner else None,
            status=status,
            source=source,
            tags=tags or [],
            ai_suggestions=ai_suggestions or {},
            title="Untitled use case",
        )
        uc.template = template
        apply_fields(uc, template, raw_fields)
        session.add(uc)
        try:
            async with session.begin_nested():
                await session.flush()
            break
        except IntegrityError:
            session.expunge(uc)
            if attempt == 4:
                raise
    session.add(UseCaseVersion(use_case_id=uc.id, version=1, fields=uc.fields,
                               changed_by_id=owner.id if owner else None, change_note=f"Created via {source}"))
    await session.flush()
    return uc


async def update_fields(session: AsyncSession, uc: UseCase, raw_fields: dict, user: User | None,
                        note: str = "") -> UseCase:
    apply_fields(uc, uc.template, raw_fields)
    last = await session.scalar(
        select(func.max(UseCaseVersion.version)).where(UseCaseVersion.use_case_id == uc.id)
    )
    session.add(UseCaseVersion(use_case_id=uc.id, version=(last or 0) + 1, fields=uc.fields,
                               changed_by_id=user.id if user else None, change_note=note or "Updated"))
    await session.flush()
    return uc


async def validate_risk(session: AsyncSession, uc: UseCase, reviewer: User, tier: str, rationale: str,
                        ai_suggestion: dict | None = None) -> RiskValidation:
    """Record a reviewer's validation (append-only) and project it onto the use case.
    Four-eyes: the validator cannot be the owner of the use case."""
    if reviewer.id == uc.owner_id:
        raise PermissionError("Four-eyes rule: the owner of a use case cannot validate its own risk tier")
    rv = RiskValidation(use_case_id=uc.id, tier=tier, rationale=rationale[:4000], validated_by_id=reviewer.id,
                        ai_suggestion=ai_suggestion or {}, fields_hash=risk_fields_hash(uc.template, uc.fields),
                        template_version=uc.template.version)
    session.add(rv)
    uc.risk_tier = tier
    uc.risk_fields_hash = rv.fields_hash
    uc.risk_stale = False
    uc.fields = {**uc.fields, "eu_ai_act_tier": tier, "eu_ai_act_rationale": rationale[:4000]}
    last = await session.scalar(select(func.max(UseCaseVersion.version)).where(UseCaseVersion.use_case_id == uc.id))
    session.add(UseCaseVersion(use_case_id=uc.id, version=(last or 0) + 1, fields=uc.fields,
                               changed_by_id=reviewer.id, change_note=f"Risk tier validated: {tier}"))
    await session.flush()
    return rv


def soft_delete(uc: UseCase, user: User) -> None:
    uc.deleted_at = datetime.now(timezone.utc)
    uc.deleted_by_id = user.id


async def analyze_in_background(use_case_id: str, use_llm: bool = True) -> None:
    """Runs after the request returns: embedding, entities, structural + semantic relations."""
    async with SessionLocal() as session:
        uc = await session.get(UseCase, use_case_id)
        if not uc or uc.deleted_at:
            return
        try:
            stats = await analysis.analyze_use_case(session, uc, use_llm=use_llm)
            await session.commit()
            log.info("Analyzed %s: %s", uc.ref, stats)
        except Exception:  # never crash the worker
            await session.rollback()
            log.exception("Analysis failed for %s", use_case_id)
