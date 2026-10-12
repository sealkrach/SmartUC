"""Portfolio-level endpoints: dashboard, graph, analysis runs, relation inbox."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_role
from ..db import SessionLocal, get_session
from ..models import AnalysisRun, Entity, Relation, RelationStatus, Role, UseCase, UseCaseEntity, User
from ..schemas import GraphOut, RelationOut
from ..services import analysis

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("/dashboard")
async def dashboard(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    ucs = (await session.scalars(select(UseCase))).all()
    by = lambda key: _count(ucs, key)  # noqa: E731
    suggested = await session.scalar(select(func.count()).select_from(Relation)
                                     .where(Relation.status == RelationStatus.suggested.value))
    rel_types = await session.execute(select(Relation.type, func.count()).where(Relation.status != "dismissed")
                                      .group_by(Relation.type))
    top_entities = await session.execute(
        select(Entity.kind, Entity.name, func.count(UseCaseEntity.id).label("n"))
        .join(UseCaseEntity, UseCaseEntity.entity_id == Entity.id)
        .group_by(Entity.id, Entity.kind, Entity.name).order_by(func.count(UseCaseEntity.id).desc()).limit(12)
    )
    scored = [u for u in ucs if u.priority_score is not None]
    top = sorted(scored, key=lambda u: -(u.priority_score or 0))[:8]
    last_run = await session.scalar(select(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(1))
    return {
        "total": len(ucs),
        "by_status": by(lambda u: u.status),
        "by_risk_tier": by(lambda u: u.risk_tier or "unclassified"),
        "by_business_unit": by(lambda u: u.business_unit or "—"),
        "by_capability": by(lambda u: (u.fields or {}).get("ai_capability") or "—"),
        "avg_completeness": round(sum(u.completeness for u in ucs) / len(ucs), 3) if ucs else 0,
        "suggested_relations": suggested or 0,
        "relations_by_type": {t: n for t, n in rel_types.all()},
        "top_entities": [{"kind": k, "name": n, "use_cases": c} for k, n, c in top_entities.all()],
        "top_priority": [{"id": u.id, "ref": u.ref, "title": u.title, "priority_score": u.priority_score,
                          "value_score": u.value_score, "feasibility_score": u.feasibility_score,
                          "status": u.status} for u in top],
        "matrix": [{"id": u.id, "ref": u.ref, "title": u.title, "value": u.value_score,
                    "feasibility": u.feasibility_score, "status": u.status, "risk_tier": u.risk_tier}
                   for u in scored if u.value_score is not None and u.feasibility_score is not None],
        "last_analysis": {"status": last_run.status, "started_at": last_run.started_at, "stats": last_run.stats}
        if last_run else None,
    }


def _count(items, keyfn) -> dict[str, int]:
    out: dict[str, int] = {}
    for it in items:
        k = keyfn(it)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


@router.get("/relations", response_model=list[RelationOut])
async def relation_inbox(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user),
                         status: str = "suggested", limit: int = 200):
    stmt = select(Relation).order_by(Relation.confidence.desc()).limit(limit)
    if status != "all":
        stmt = stmt.where(Relation.status == status)
    rels = (await session.scalars(stmt)).all()
    ids = {r.source_id for r in rels} | {r.target_id for r in rels}
    by_id = {u.id: u for u in (await session.scalars(select(UseCase).where(UseCase.id.in_(ids)))).all()} if ids else {}
    out = []
    for r in rels:
        o = RelationOut.model_validate(r)
        s, t = by_id.get(r.source_id), by_id.get(r.target_id)
        o.source_ref, o.source_title = (s.ref, s.title) if s else (None, None)
        o.target_ref, o.target_title = (t.ref, t.title) if t else (None, None)
        out.append(o)
    return out


@router.get("/graph", response_model=GraphOut)
async def graph(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user),
                include_entities: bool = True, include_suggested: bool = True, min_shared: int = 2):
    ucs = (await session.scalars(select(UseCase))).all()
    nodes = [{"id": u.id, "kind": "usecase", "ref": u.ref, "label": u.title, "status": u.status,
              "risk_tier": u.risk_tier, "business_unit": u.business_unit, "priority": u.priority_score,
              "capability": (u.fields or {}).get("ai_capability")} for u in ucs]
    stmt = select(Relation).where(Relation.status != RelationStatus.dismissed.value)
    if not include_suggested:
        stmt = stmt.where(Relation.status == RelationStatus.confirmed.value)
    rels = (await session.scalars(stmt)).all()
    edges = []
    for r in rels:
        if r.type == "shares_entity":
            continue  # represented via entity nodes below (or omitted)
        edges.append({"id": r.id, "source": r.source_id, "target": r.target_id, "type": r.type,
                      "status": r.status, "confidence": r.confidence, "explanation": r.explanation})
    if include_entities:
        counts = await session.execute(select(UseCaseEntity.entity_id, func.count(func.distinct(UseCaseEntity.use_case_id)))
                                       .group_by(UseCaseEntity.entity_id))
        shared_ids = {eid for eid, n in counts.all() if n >= min_shared}
        if shared_ids:
            ents = (await session.scalars(select(Entity).where(Entity.id.in_(shared_ids)))).all()
            for e in ents:
                nodes.append({"id": e.id, "kind": "entity", "entity_kind": e.kind, "label": e.name})
            links = (await session.scalars(select(UseCaseEntity).where(UseCaseEntity.entity_id.in_(shared_ids)))).all()
            seen = set()
            for l in links:
                key = (l.use_case_id, l.entity_id)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"id": l.id, "source": l.use_case_id, "target": l.entity_id, "type": "uses_entity",
                              "status": "confirmed", "confidence": 1.0, "explanation": l.field_id, "role": l.role})
    return GraphOut(nodes=nodes, edges=edges)


async def _run_portfolio(run_id: str, use_llm: bool) -> None:
    async with SessionLocal() as session:
        run = await session.get(AnalysisRun, run_id)
        try:
            stats = await analysis.analyze_portfolio(session, use_llm=use_llm)
            run.stats = stats
            run.status = "done"
        except Exception as e:
            run.status = "failed"
            run.error = str(e)[:1000]
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()


@router.post("/analyze")
async def analyze_all(bg: BackgroundTasks, session: AsyncSession = Depends(get_session),
                      _: User = Depends(require_role(Role.reviewer)), use_llm: bool = True):
    running = await session.scalar(select(AnalysisRun).where(AnalysisRun.status == "running"))
    if running:
        raise HTTPException(409, "An analysis is already running")
    run = AnalysisRun(scope="portfolio", status="running", stats={})
    session.add(run)
    await session.commit()
    bg.add_task(_run_portfolio, run.id, use_llm)
    return {"run_id": run.id, "status": "running"}


@router.get("/analyze/runs")
async def runs(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    rs = (await session.scalars(select(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(20))).all()
    return [{"id": r.id, "scope": r.scope, "status": r.status, "stats": r.stats, "error": r.error,
             "started_at": r.started_at, "finished_at": r.finished_at} for r in rs]


@router.get("/entities")
async def list_entities(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user),
                        kind: str | None = None):
    stmt = (select(Entity, func.count(UseCaseEntity.id)).outerjoin(UseCaseEntity, UseCaseEntity.entity_id == Entity.id)
            .group_by(Entity.id).order_by(func.count(UseCaseEntity.id).desc()))
    if kind:
        stmt = stmt.where(Entity.kind == kind)
    rows = (await session.execute(stmt)).all()
    return [{"id": e.id, "kind": e.kind, "name": e.name, "aliases": e.aliases, "use_cases": n} for e, n in rows]
