"""Portfolio intelligence: entities, overlap detection, dependency inference.

Two complementary signals:
1. Structural — use cases that share entities (systems, data sources, teams, models) get a
   `shares_entity` relation. Deterministic, no LLM.
2. Semantic — embeddings surface candidate pairs above a cosine threshold; an LLM judge classifies each
   pair as duplicate / overlap / complementary / depends_on / enables / none, with an explanation and a
   suggested action. Human-reviewed relations (confirmed/dismissed) are never overwritten.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Entity, Relation, RelationStatus, RelationType, UseCase, UseCaseEntity
from . import llm
from .templates import embedding_text, entity_fields, redact_for_llm

log = logging.getLogger(__name__)

_CANON_STRIP = re.compile(r"[^a-z0-9]+")
_CANON_DROP = {"the", "system", "platform", "tool", "application", "app", "data", "database", "db", "team"}


def canonicalize(name: str) -> str:
    tokens = [t for t in _CANON_STRIP.sub(" ", name.lower()).split() if t not in _CANON_DROP]
    return " ".join(tokens) or name.lower().strip()


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

async def sync_entities(session: AsyncSession, uc: UseCase) -> list[UseCaseEntity]:
    """Rebuild the use case's entity links from its entity_list fields."""
    await session.execute(delete(UseCaseEntity).where(UseCaseEntity.use_case_id == uc.id))
    links: list[UseCaseEntity] = []
    seen: set[tuple[str, str]] = set()
    for f in entity_fields(uc.template.definition):
        kind = f.get("entity_kind", "system")
        role = "produces" if f["id"].startswith("target") else "uses"
        for raw in uc.fields.get(f["id"]) or []:
            canonical = canonicalize(str(raw))
            if not canonical:
                continue
            ent = await session.scalar(select(Entity).where(Entity.kind == kind, Entity.canonical == canonical))
            if not ent:
                ent = Entity(kind=kind, name=str(raw).strip(), canonical=canonical, aliases=[])
                session.add(ent)
                await session.flush()
            elif str(raw).strip() != ent.name and str(raw).strip() not in (ent.aliases or []):
                ent.aliases = [*(ent.aliases or []), str(raw).strip()]
            key = (ent.id, f["id"])
            if key in seen:
                continue
            seen.add(key)
            link = UseCaseEntity(use_case_id=uc.id, entity_id=ent.id, field_id=f["id"], role=role)
            session.add(link)
            links.append(link)
    await session.flush()
    return links


async def structural_relations(session: AsyncSession, uc: UseCase) -> int:
    """Create shares_entity relations with every other use case sharing an entity."""
    my_links = (await session.scalars(select(UseCaseEntity).where(UseCaseEntity.use_case_id == uc.id))).all()
    my_entities = {l.entity_id for l in my_links}
    if not my_entities:
        return 0
    others = (
        await session.scalars(
            select(UseCaseEntity).join(UseCase, UseCase.id == UseCaseEntity.use_case_id)
            .where(UseCaseEntity.entity_id.in_(my_entities), UseCaseEntity.use_case_id != uc.id,
                   UseCase.deleted_at.is_(None))
        )
    ).all()
    # Remove stale structural relations for this UC, then re-create
    await session.execute(
        delete(Relation).where(
            Relation.type == RelationType.shares_entity.value,
            Relation.origin == "structural",
            or_(Relation.source_id == uc.id, Relation.target_id == uc.id),
        )
    )
    created = 0
    seen: set[tuple[str, str]] = set()
    for link in others:
        a, b = sorted([uc.id, link.use_case_id])
        key = (f"{a}:{b}", link.entity_id)
        if key in seen:
            continue
        seen.add(key)
        ent = link.entity
        session.add(
            Relation(
                source_id=a,
                target_id=b,
                type=RelationType.shares_entity.value,
                status=RelationStatus.confirmed.value,
                confidence=1.0,
                explanation=f"Both use the {ent.kind} '{ent.name}'.",
                suggested_action="Coordinate integration work and reuse connectors / data pipelines.",
                entity_id=link.entity_id,
                origin="structural",
            )
        )
        created += 1
    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Embeddings & semantic candidates
# ---------------------------------------------------------------------------

async def ensure_embedding(session: AsyncSession, uc: UseCase) -> bool:
    """(Re)compute the embedding when the text or the configured model changed. Provenance is stored in
    `embedding_model`; a provider failure keeps the previous vector and logs (no silent hash fallback)."""
    model = get_settings().embedding_model
    text = embedding_text(uc.template.definition, redact_for_llm(uc.template.definition, uc.fields), uc.title,
                          uc.summary)
    h = llm.text_hash(text)
    if uc.embedding is not None and uc.embedding_text_hash == h and uc.embedding_model == model:
        return False
    try:
        vectors = await llm.embed([text or uc.title])
    except llm.EmbeddingError as e:
        log.error("Keeping previous embedding for %s: %s", uc.ref, e)
        return False
    uc.embedding = vectors[0]
    uc.embedding_text_hash = h
    uc.embedding_model = model
    await session.flush()
    return True


async def semantic_candidates(session: AsyncSession, uc: UseCase) -> list[tuple[UseCase, float]]:
    s = get_settings()
    if uc.embedding is None:
        return []
    others = (
        await session.scalars(select(UseCase).where(UseCase.id != uc.id, UseCase.embedding.is_not(None),
                                                    UseCase.deleted_at.is_(None),
                                                    UseCase.embedding_model == uc.embedding_model))
    ).all()
    scored = [(o, llm.cosine(uc.embedding, o.embedding)) for o in others]
    scored = [(o, sim) for o, sim in scored if sim >= s.candidate_threshold]
    scored.sort(key=lambda x: -x[1])
    return scored[: s.overlap_max_candidates]


JUDGE_SYSTEM = """You are a senior AI portfolio architect. Compare use case A with each candidate B and decide the relationship:
- duplicate: same problem for the same users; they should be merged into one initiative.
- overlap: significant shared scope (same process step, same users or same output) but not identical; teams should align.
- complementary: adjacent problems that could share components (data pipeline, model, UI) — reuse opportunity.
- depends_on: A needs B to exist first (B produces data, a model, a platform or a decision A requires).
- enables: A produces something B needs (the inverse of depends_on).
- none: superficially similar wording but no meaningful relationship.
Be strict: shared generic technology (e.g. both use an LLM) is NOT overlap. Shared business process, users, data or outputs is.
For each candidate output: {"id": <candidate id>, "type": <one of the above>, "confidence": <0..1>,
"explanation": <1-2 sentences, specific>, "suggested_action": <one concrete recommendation or "">}
Output JSON: {"results": [ ... ]}"""


def _brief(uc: UseCase) -> dict:
    f = uc.fields or {}
    return {
        "id": uc.id,
        "ref": uc.ref,
        "title": uc.title,
        "summary": uc.summary or f.get("summary"),
        "business_unit": uc.business_unit,
        "status": uc.status,
        "problem": f.get("problem_statement"),
        "solution": f.get("solution_description"),
        "users": f.get("target_users"),
        "data_sources": f.get("data_sources"),
        "systems": (f.get("source_systems") or []) + (f.get("target_systems") or []),
        "dependencies_notes": f.get("dependencies_notes"),
    }


async def judge_pairs(uc: UseCase, candidates: list[tuple[UseCase, float]]) -> list[dict]:
    if not candidates:
        return []
    user = (
        "USE CASE A:\n" + llm.untrusted("use case A", json.dumps(_brief(uc), ensure_ascii=False)) + "\n\n"
        "CANDIDATES B:\n" + llm.untrusted("candidates", json.dumps([_brief(c) for c, _ in candidates], ensure_ascii=False))
    )
    result = await llm.complete_json(JUDGE_SYSTEM, user, max_tokens=3000)
    valid = {t.value for t in RelationType} | {"none"}
    out = []
    for r in result.get("results") or []:
        if r.get("type") in valid and r.get("id"):
            out.append(r)
    return out


async def semantic_relations(session: AsyncSession, uc: UseCase, use_llm: bool = True) -> dict:
    candidates = await semantic_candidates(session, uc)
    stats = {"candidates": len(candidates), "created": 0, "updated": 0, "llm": use_llm}
    if not candidates:
        return stats
    sim_by_id = {c.id: sim for c, sim in candidates}

    judgements: list[dict]
    if use_llm:
        try:
            judgements = await judge_pairs(uc, candidates)
        except llm.LLMUnavailable as e:
            log.warning("LLM judge unavailable (%s) — falling back to similarity-only overlaps", e)
            stats["llm"] = False
            judgements = []
            use_llm = False
    if not use_llm:
        judgements = [
            {"id": c.id, "type": "overlap", "confidence": round(sim, 2),
             "explanation": f"High textual similarity ({sim:.2f}). LLM judge not available — review manually.",
             "suggested_action": "Configure an LLM to get a precise classification."}
            for c, sim in candidates if sim >= get_settings().fallback_threshold
        ]

    judged_ids = set()
    for j in judgements:
        other_id = j["id"]
        if other_id not in sim_by_id:
            continue
        judged_ids.add(other_id)
        rtype = j["type"]
        # relations are directional for depends_on/enables; symmetric otherwise (store with sorted ids)
        if rtype == "enables":
            src, tgt, rtype = other_id, uc.id, "depends_on"   # B depends on A
        elif rtype == "depends_on":
            src, tgt = uc.id, other_id
        else:
            src, tgt = sorted([uc.id, other_id])

        existing = (
            await session.scalars(
                select(Relation).where(
                    Relation.origin == "ai",
                    or_(
                        (Relation.source_id == uc.id) & (Relation.target_id == other_id),
                        (Relation.source_id == other_id) & (Relation.target_id == uc.id),
                    ),
                )
            )
        ).all()
        reviewed = [r for r in existing if r.status != RelationStatus.suggested.value]
        if reviewed:
            continue  # humans decided; do not touch
        for r in existing:  # drop stale AI suggestions for this pair
            await session.delete(r)
        await session.flush()
        if rtype == "none":
            continue
        session.add(
            Relation(
                source_id=src,
                target_id=tgt,
                type=rtype,
                status=RelationStatus.suggested.value,
                confidence=float(j.get("confidence") or 0),
                similarity=sim_by_id[other_id],
                explanation=str(j.get("explanation") or ""),
                suggested_action=str(j.get("suggested_action") or ""),
                origin="ai",
            )
        )
        stats["created"] += 1
    await session.flush()
    return stats


async def analyze_use_case(session: AsyncSession, uc: UseCase, use_llm: bool = True) -> dict:
    await ensure_embedding(session, uc)
    await sync_entities(session, uc)
    structural = await structural_relations(session, uc)
    semantic = await semantic_relations(session, uc, use_llm=use_llm)
    uc.last_analyzed_at = datetime.now(timezone.utc)
    await session.flush()
    return {"structural_relations": structural, **semantic}


async def analyze_portfolio(session: AsyncSession, use_llm: bool = True) -> dict:
    ucs = (await session.scalars(select(UseCase).where(UseCase.deleted_at.is_(None)))).all()
    totals = {"use_cases": len(ucs), "structural_relations": 0, "created": 0, "candidates": 0}
    for uc in ucs:
        await ensure_embedding(session, uc)
        await sync_entities(session, uc)
    for uc in ucs:
        totals["structural_relations"] += await structural_relations(session, uc)
    for uc in ucs:
        st = await semantic_relations(session, uc, use_llm=use_llm)
        totals["created"] += st["created"]
        totals["candidates"] += st["candidates"]
        uc.last_analyzed_at = datetime.now(timezone.utc)
    await session.commit()
    return totals
