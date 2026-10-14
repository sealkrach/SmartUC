"""CSV / XLSX import with AI-assisted column mapping and optional AI enrichment."""
from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from ..db import SessionLocal
from ..models import ImportJob, Status, Template, User
from . import analysis, llm
from .normalizer import normalize_text
from .templates import field_map, schema_for_llm
from datetime import datetime, timezone
from .usecases import create_use_case

log = logging.getLogger(__name__)


def is_orphaned(job: ImportJob) -> bool:
    """A job still 'running' without a heartbeat for longer than the lease was interrupted (restart, crash)."""
    from ..config import get_settings

    last = job.heartbeat_at or job.started_at or job.created_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last).total_seconds() > get_settings().job_lease_seconds


def read_table(path: Path) -> tuple[list[str], list[dict]]:
    if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            return [], []
        header = [str(h).strip() if h is not None else f"col_{i+1}" for i, h in enumerate(rows[0])]
        data = []
        for r in rows[1:]:
            if r is None or all(v is None or str(v).strip() == "" for v in r):
                continue
            data.append({header[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(header)})
        return header, data
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    data = [dict(r) for r in reader if any((v or "").strip() for v in r.values())]
    return list(reader.fieldnames or []), data


MAPPING_SYSTEM = """You map spreadsheet columns to the fields of an AI use case template.
For each column, pick the single best field id, or null if no field fits (the content will still be used as
free text for AI enrichment). Several columns may map to the same field only if they are clearly the same concept.
Output JSON: {"mapping": {<column>: <field_id or null>}, "notes": <string>}"""


async def suggest_mapping(definition: dict, columns: list[str], sample_rows: list[dict]) -> dict:
    fm = field_map(definition)
    # heuristic first (exact / contains label match), then LLM for the rest
    mapping: dict[str, str | None] = {}
    for col in columns:
        c = col.strip().lower().replace("_", " ")
        hit = None
        for fid, f in fm.items():
            if c in (fid.replace("_", " "), f.get("label", "").lower()):
                hit = fid
                break
        mapping[col] = hit
    unresolved = [c for c, v in mapping.items() if v is None]
    if unresolved:
        try:
            user = (
                f"TEMPLATE FIELDS:\n{schema_for_llm(definition)}\n\nCOLUMNS: {json.dumps(unresolved, ensure_ascii=False)}\n"
                "SAMPLE ROWS:\n" + llm.untrusted("sample rows", json.dumps(sample_rows[:3], ensure_ascii=False)[:6000])
            )
            result = await llm.complete_json(MAPPING_SYSTEM, user, fast=True)
            for col, fid in (result.get("mapping") or {}).items():
                if col in mapping and (fid is None or fid in fm):
                    mapping[col] = fid
        except llm.LLMUnavailable as e:
            log.warning("Mapping suggestion without LLM: %s", e)
    return mapping


async def run_import(job_id: str) -> None:
    async with SessionLocal() as session:
        job = await session.get(ImportJob, job_id)
        if not job:
            return
        template = await session.get(Template, job.template_id)
        user = await session.get(User, job.user_id)
        job.status = "running"
        job.progress = 0
        job.errors = []
        job.created_ids = []
        job.started_at = datetime.now(timezone.utc)
        job.heartbeat_at = job.started_at
        await session.commit()
        try:
            _, rows = read_table(Path(job.stored_path))
            total = len(rows) or 1
            fm = field_map(template.definition)
            for i, row in enumerate(rows):
                try:
                    uc = await _import_row(session, template, user, row, job.mapping, job.ai_enrich, fm)
                    job.created_ids = [*job.created_ids, uc.id]
                except Exception as e:  # keep going, report per row
                    job.errors = [*job.errors, {"row": i + 2, "error": str(e)[:300]}]
                job.progress = int(100 * (i + 1) / total)
                job.heartbeat_at = datetime.now(timezone.utc)
                await session.commit()
            # structural + semantic analysis once everything is in
            await analysis.analyze_portfolio(session, use_llm=job.ai_enrich)
            job.status = "done"
        except Exception as e:
            job.status = "failed"
            job.errors = [*job.errors, {"row": 0, "error": str(e)[:500]}]
        job.progress = 100
        await session.commit()


async def _import_row(session: AsyncSession, template: Template, user: User | None, row: dict,
                      mapping: dict, ai_enrich: bool, fm: dict):
    mapped: dict = {}
    leftovers: list[str] = []
    for col, value in row.items():
        if value is None or str(value).strip() == "":
            continue
        fid = mapping.get(col)
        if fid and fid in fm:
            if fid in mapped and fm[fid]["type"] in {"list", "entity_list", "multi_enum", "longtext"}:
                mapped[fid] = f"{mapped[fid]}\n{value}"
            else:
                mapped[fid] = value
        else:
            leftovers.append(f"{col}: {value}")
    fields = mapped
    suggestions: dict = {}
    if ai_enrich:
        text = "\n".join(f"{fm[k]['label']}: {v}" for k, v in mapped.items() if k in fm)
        if leftovers:
            text += "\nAdditional information:\n" + "\n".join(leftovers)
        try:
            result = await normalize_text(template.definition, text, existing=mapped)
            fields = result["fields"]
            suggestions = {"assumptions": result["assumptions"], "open_questions": result["open_questions"]}
        except llm.LLMUnavailable as e:
            suggestions = {"warning": f"AI enrichment skipped: {e}"}
    if not fields.get("title"):
        fields["title"] = next((str(v) for v in row.values() if v), "Imported use case")[:120]
    # Imported records land as DRAFT (F-11): a human reviews the AI-normalised canvas against the original row
    # (kept in ai_suggestions.import_row) before submitting it to the governance workflow.
    suggestions["import_row"] = {str(k)[:100]: str(v)[:2000] for k, v in row.items() if v not in (None, "")}
    return await create_use_case(session, template, fields, user, status=Status.draft.value,
                                 source="import", ai_suggestions=suggestions)
