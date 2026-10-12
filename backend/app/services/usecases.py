"""Use case lifecycle helpers shared by API, intake, import and seed."""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import Status, Template, UseCase, UseCaseVersion, User
from . import analysis
from .templates import completeness, compute_scores, normalize_fields

log = logging.getLogger(__name__)


async def next_ref(session: AsyncSession) -> str:
    n = await session.scalar(select(func.count()).select_from(UseCase))
    candidate = (n or 0) + 1
    while await session.scalar(select(UseCase.id).where(UseCase.ref == f"UC-{candidate:04d}")):
        candidate += 1
    return f"UC-{candidate:04d}"


def apply_fields(uc: UseCase, template: Template, raw_fields: dict) -> None:
    definition = template.definition
    fields = normalize_fields(definition, raw_fields)
    uc.fields = fields
    uc.title = (fields.get("title") or uc.title or "Untitled use case")[:255]
    uc.summary = fields.get("summary") or uc.summary or ""
    uc.business_unit = (fields.get("business_unit") or uc.business_unit or "")[:255]
    uc.completeness, uc.missing_required = completeness(definition, fields)
    scores = compute_scores(definition, fields)
    uc.value_score = scores["value_score"]
    uc.feasibility_score = scores["feasibility_score"]
    uc.priority_score = scores["priority_score"]
    if fields.get("eu_ai_act_tier"):
        uc.risk_tier = fields["eu_ai_act_tier"]


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
    await session.flush()
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


async def analyze_in_background(use_case_id: str, use_llm: bool = True) -> None:
    """Runs after the request returns: embedding, entities, structural + semantic relations."""
    async with SessionLocal() as session:
        uc = await session.get(UseCase, use_case_id)
        if not uc:
            return
        try:
            stats = await analysis.analyze_use_case(session, uc, use_llm=use_llm)
            await session.commit()
            log.info("Analyzed %s: %s", uc.ref, stats)
        except Exception:  # never crash the worker
            await session.rollback()
            log.exception("Analysis failed for %s", use_case_id)
