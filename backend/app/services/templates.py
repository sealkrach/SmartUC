"""Template engine: load YAML templates, validate/coerce fields, compute completeness & scores."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Template

ENTITY_TYPES = {"entity_list"}
LIST_TYPES = {"list", "multi_enum", "entity_list"}


def iter_fields(definition: dict) -> list[dict]:
    out: list[dict] = []
    for section in definition.get("sections", []):
        for f in section.get("fields", []):
            out.append({**f, "section": section["id"], "section_title": section.get("title", section["id"])})
    return out


def field_map(definition: dict) -> dict[str, dict]:
    return {f["id"]: f for f in iter_fields(definition)}


def is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def coerce_value(field: dict, value: Any) -> Any:
    """Best-effort coercion of a raw value into the field's type. Returns None when unusable."""
    if is_empty(value):
        return None
    t = field["type"]
    if t in LIST_TYPES:
        if isinstance(value, str):
            parts = [p.strip(" -•*\t") for p in value.replace(";", "\n").replace(",", "\n").split("\n")]
            value = [p for p in parts if p]
        elif not isinstance(value, list):
            value = [str(value)]
        value = [str(v).strip() for v in value if not is_empty(v)]
        if t == "multi_enum":
            opts = set(field.get("options", []))
            norm = []
            for v in value:
                key = v.lower().replace(" ", "_").replace("-", "_")
                if key in opts:
                    norm.append(key)
            value = norm
        return value or None
    if t == "enum":
        key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
        opts = field.get("options", [])
        if key in opts:
            return key
        # tolerate case differences for things like effort sizes
        for o in opts:
            if str(o).lower() == key:
                return o
        return None
    if t == "number":
        try:
            if isinstance(value, str):
                cleaned = "".join(ch for ch in value if ch.isdigit() or ch in ".-")
                value = float(cleaned) if cleaned else None
            num = float(value)
        except (TypeError, ValueError):
            return None
        if num is None:
            return None
        if "min" in field:
            num = max(float(field["min"]), num)
        if "max" in field:
            num = min(float(field["max"]), num)
        return int(num) if num.is_integer() else num
    if t in {"text", "longtext", "person", "date"}:
        return str(value).strip()
    return value


def normalize_fields(definition: dict, raw: dict) -> dict:
    """Keep only known fields, coerce types, drop empties."""
    fm = field_map(definition)
    out: dict[str, Any] = {}
    for fid, value in (raw or {}).items():
        if fid not in fm:
            continue
        coerced = coerce_value(fm[fid], value)
        if coerced is not None:
            out[fid] = coerced
    return out


def completeness(definition: dict, fields: dict) -> tuple[float, list[str]]:
    """Weighted completeness in 0..1 and list of missing required field ids."""
    total = 0.0
    got = 0.0
    missing: list[str] = []
    for f in iter_fields(definition):
        w = float(f.get("weight", 1))
        total += w
        if not is_empty(fields.get(f["id"])):
            got += w
        elif f.get("required"):
            missing.append(f["id"])
    return (round(got / total, 3) if total else 0.0), missing


def _term_score(term: dict, fields: dict) -> float | None:
    value = fields.get(term["field"])
    if is_empty(value):
        return None
    if "mapping" in term:
        return term["mapping"].get(str(value))
    if "count_scale" in term:
        n = len(value) if isinstance(value, list) else 1
        return min(1.0, n / float(term["count_scale"].get("max", 4)))
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if "scale" in term:
        lo, hi = float(term["scale"]["min"]), float(term["scale"]["max"])
        s = (num - lo) / (hi - lo) if hi > lo else 0.0
    elif "log_scale" in term:
        lo, hi = float(term["log_scale"]["min"]), float(term["log_scale"]["max"])
        if num <= 0:
            return 0.0
        s = (math.log10(max(num, lo)) - math.log10(lo)) / (math.log10(hi) - math.log10(lo))
    else:
        return None
    s = max(0.0, min(1.0, s))
    return 1.0 - s if term.get("invert") else s


def _axis(terms: list[dict], fields: dict) -> float | None:
    num = 0.0
    den = 0.0
    for t in terms:
        s = _term_score(t, fields)
        if s is None:
            continue
        w = float(t.get("weight", 1))
        num += s * w
        den += w
    if den == 0:
        return None
    return round(100 * num / den, 1)


def compute_scores(definition: dict, fields: dict) -> dict[str, float | None]:
    sc = definition.get("scoring", {})
    value = _axis(sc.get("value", []), fields)
    feas = _axis(sc.get("feasibility", []), fields)
    prio = None
    if value is not None and feas is not None:
        pw = sc.get("priority", {})
        vw = float(pw.get("value_weight", 0.6))
        fw = float(pw.get("feasibility_weight", 0.4))
        prio = round(value * vw + feas * fw, 1)
    return {"value_score": value, "feasibility_score": feas, "priority_score": prio}


def embedding_text(definition: dict, fields: dict, title: str = "", summary: str = "") -> str:
    fm = field_map(definition)
    parts: list[str] = []
    for fid in definition.get("embedding_fields", ["title", "summary"]):
        v = fields.get(fid)
        if fid == "title" and is_empty(v):
            v = title
        if fid == "summary" and is_empty(v):
            v = summary
        if is_empty(v):
            continue
        label = fm.get(fid, {}).get("label", fid)
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        parts.append(f"{label}: {v}")
    return "\n".join(parts)


def entity_fields(definition: dict) -> list[dict]:
    return [f for f in iter_fields(definition) if f["type"] in ENTITY_TYPES]


def schema_for_llm(definition: dict, only: list[str] | None = None) -> str:
    """Compact, LLM-friendly description of the fields."""
    lines = []
    for f in iter_fields(definition):
        if only and f["id"] not in only:
            continue
        spec = f"- {f['id']} ({f['type']}"
        if f.get("options"):
            spec += f"; one of: {', '.join(map(str, f['options']))}"
            if f["type"] == "multi_enum":
                spec += "; may select several"
        if f.get("min") is not None:
            spec += f"; {f['min']}..{f['max']}"
        spec += ")"
        if f.get("required"):
            spec += " [required]"
        spec += f": {f.get('label', '')}"
        if f.get("ai_hint"):
            spec += f" — {f['ai_hint']}"
        lines.append(spec)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence: sync YAML files on disk into the templates table.
# ---------------------------------------------------------------------------

def load_yaml_templates(directory: Path | None = None) -> list[dict]:
    directory = directory or get_settings().templates_dir
    defs = []
    for path in sorted(Path(directory).glob("*.y*ml")):
        with open(path, encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
        if d and "key" in d and "sections" in d:
            d.setdefault("version", 1)
            d.setdefault("name", d["key"])
            defs.append(d)
    return defs


async def sync_templates(session: AsyncSession) -> list[Template]:
    existing = {(t.key, t.version): t for t in (await session.scalars(select(Template))).all()}
    out = []
    any_default = any(t.is_default for t in existing.values())
    for d in load_yaml_templates():
        key = (d["key"], int(d["version"]))
        if key in existing:
            t = existing[key]
            t.definition = d
            t.name = d["name"]
            t.description = d.get("description", "")
        else:
            t = Template(
                key=d["key"],
                version=int(d["version"]),
                name=d["name"],
                description=d.get("description", ""),
                is_default=bool(d.get("is_default")) and not any_default,
                definition=d,
            )
            session.add(t)
            if t.is_default:
                any_default = True
        out.append(t)
    await session.commit()
    return out


async def get_default_template(session: AsyncSession) -> Template:
    t = await session.scalar(select(Template).where(Template.is_default.is_(True)))
    if not t:
        t = await session.scalar(select(Template).order_by(Template.created_at))
    if not t:
        raise RuntimeError("No template available. Add a YAML file in app/templates.")
    return t
