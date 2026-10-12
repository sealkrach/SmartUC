from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import get_current_user, require_role
from ..config import get_settings
from ..db import get_session
from ..models import ImportJob, Role, Template, User
from ..schemas import ImportJobOut, ImportMappingIn
from ..services import importer
from ..services.templates import get_default_template

router = APIRouter(prefix="/imports", tags=["imports"])


@router.get("", response_model=list[ImportJobOut])
async def list_jobs(session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    return (await session.scalars(select(ImportJob).order_by(ImportJob.created_at.desc()).limit(30))).all()


@router.post("", response_model=ImportJobOut, status_code=201)
async def upload(file: UploadFile = File(...), template_id: str | None = Form(None),
                 session: AsyncSession = Depends(get_session), user: User = Depends(require_role(Role.reviewer))):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xlsm", ".tsv", ".txt"}:
        raise HTTPException(400, "Upload a .csv or .xlsx file")
    s = get_settings()
    s.uploads_dir.mkdir(parents=True, exist_ok=True)
    stored = s.uploads_dir / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(await file.read())
    try:
        columns, rows = importer.read_table(stored)
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")
    if not columns:
        raise HTTPException(400, "No header row found")
    template = await session.get(Template, template_id) if template_id else None
    template = template or await get_default_template(session)
    mapping = await importer.suggest_mapping(template.definition, columns, rows[:5])
    job = ImportJob(user_id=user.id, template_id=template.id, filename=file.filename or stored.name,
                    stored_path=str(stored), columns=columns, sample_rows=rows[:5], row_count=len(rows),
                    mapping=mapping, status="mapped")
    session.add(job)
    await session.commit()
    return job


@router.get("/{job_id}", response_model=ImportJobOut)
async def get_job(job_id: str, session: AsyncSession = Depends(get_session), _: User = Depends(get_current_user)):
    job = await session.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Import job not found")
    return job


@router.put("/{job_id}/mapping", response_model=ImportJobOut)
async def set_mapping(job_id: str, payload: ImportMappingIn, session: AsyncSession = Depends(get_session),
                      _: User = Depends(require_role(Role.reviewer))):
    job = await session.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Import job not found")
    job.mapping = {c: payload.mapping.get(c) for c in job.columns}
    job.ai_enrich = payload.ai_enrich
    job.status = "mapped"
    await session.commit()
    return job


@router.post("/{job_id}/run", response_model=ImportJobOut)
async def run(job_id: str, bg: BackgroundTasks, session: AsyncSession = Depends(get_session),
              _: User = Depends(require_role(Role.reviewer))):
    job = await session.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "Import job not found")
    if job.status == "running":
        raise HTTPException(409, "Already running")
    job.status = "running"
    job.progress = 0
    await session.commit()
    bg.add_task(importer.run_import, job.id)
    return job
