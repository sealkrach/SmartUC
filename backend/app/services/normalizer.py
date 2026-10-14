"""AI normalization: free text -> template fields; field polishing; risk tier suggestion."""
from __future__ import annotations

import json

from . import llm
from ..config import get_settings
from .templates import field_map, normalize_fields, redact_for_llm, schema_for_llm

NORMALIZE_SYSTEM = """You are an AI portfolio analyst at a large regulated company.
You turn unstructured descriptions of AI use cases into a normalized record that follows a template.

Rules:
- Fill ONLY what the input supports. Never invent facts (names, numbers, systems). Leave unknown fields out.
- You MAY infer obvious categorical fields (ai_capability, value_drivers, data_sensitivity, urgency,
  human_in_the_loop, risk_categories) when the text clearly implies them; list each inference in "assumptions".
- entity_list fields contain short canonical names (e.g. "Salesforce", "SAP S/4HANA", "ServiceNow tickets").
- list fields are arrays of short strings. Numbers are plain numbers (EUR, people, 1-5 scales).
- Write longtext fields in crisp professional English, keeping the author's meaning. Translate if needed.
- Output JSON: {"fields": {<field_id>: <value>}, "assumptions": [<string>], "open_questions": [<string>]}
  where open_questions lists the most important missing information a reviewer would ask."""


async def normalize_text(definition: dict, text: str, existing: dict | None = None) -> dict:
    schema = schema_for_llm(definition)
    user = f"TEMPLATE FIELDS:\n{schema}\n\n"
    if existing:
        user += "ALREADY KNOWN (keep unless contradicted):\n" + llm.untrusted(
            "known fields", json.dumps(redact_for_llm(definition, existing), ensure_ascii=False)) + "\n\n"
    user += llm.untrusted("input text", text[:40000])
    result = await llm.complete_json(NORMALIZE_SYSTEM, user)
    fields = normalize_fields(definition, {**(existing or {}), **(result.get("fields") or {})})
    return {
        "fields": fields,
        "assumptions": [str(a) for a in result.get("assumptions") or []],
        "open_questions": [str(q) for q in result.get("open_questions") or []],
    }


IMPROVE_SYSTEM = """You are an expert editor of AI use case documentation for executives and architects.
Rewrite the given field value so it is precise, specific, measurable where possible, and free of jargon.
Keep every fact; do not add facts. Keep the same language as the input unless asked otherwise.
Output JSON: {"value": <rewritten value matching the field type>, "notes": <one sentence on what you changed>}"""


async def improve_field(definition: dict, field_id: str, value, context: dict, instruction: str = "") -> dict:
    fm = field_map(definition)
    f = fm.get(field_id)
    if not f:
        raise ValueError(f"Unknown field {field_id}")
    if f.get("read_only") or f.get("llm_exposable") is False:
        raise ValueError(f"Field {field_id} cannot be sent to the AI")
    ctx = redact_for_llm(definition, {k: context.get(k) for k in ("title", "summary", "problem_statement")})
    user = (
        f"FIELD: {field_id} ({f['type']}) — {f.get('label')}\n"
        f"GUIDANCE: {f.get('ai_hint', '')}\n"
        "CURRENT VALUE: " + llm.untrusted("current value", json.dumps(value, ensure_ascii=False)) + "\n"
        "USE CASE CONTEXT: " + llm.untrusted("context", json.dumps(ctx, ensure_ascii=False)) + "\n"
    )
    if instruction:
        user += "USER INSTRUCTION: " + llm.untrusted("instruction", instruction[:1000]) + "\n"
    result = await llm.complete_json(IMPROVE_SYSTEM, user, fast=True)
    coerced = normalize_fields(definition, {field_id: result.get("value")}).get(field_id)
    return {"value": coerced if coerced is not None else value, "notes": result.get("notes", "")}


RISK_SYSTEM = """You are an EU AI Act compliance analyst. Classify the AI use case into a risk tier:
- prohibited: Art. 5 practices (manipulation, social scoring, untargeted facial scraping, emotion recognition at work/school...)
- high_risk: Annex III domains (employment/HR decisions, credit scoring & creditworthiness, insurance pricing for life/health,
  essential services access, education, law enforcement, critical infrastructure safety, biometrics) or Annex I safety components
- limited_risk: transparency obligations (chatbots interacting with people, deepfakes, emotion recognition elsewhere)
- minimal_risk: everything else (internal productivity, spam filters, summarization for employees...)
- gpai_obligations_only: when the organisation only provides/uses a general-purpose model without a specific high-risk use
Be conservative: if a high_risk Annex III domain is plausible, pick high_risk and explain.
Also list the risk categories most relevant and 2-4 concrete mitigations.
Output JSON: {"eu_ai_act_tier": <tier>, "rationale": <2-4 sentences citing the relevant article/annex>,
"risk_categories": [<from: bias_fairness, privacy, security, hallucination_accuracy, explainability, safety, intellectual_property, vendor_lock_in, model_drift>],
"mitigations": [<string>], "confidence": <0..1>}"""


async def suggest_risk(definition: dict, fields: dict) -> dict:
    keep = {k: v for k, v in fields.items() if k in {
        "title", "summary", "problem_statement", "solution_description", "target_users", "ai_capability",
        "human_in_the_loop", "data_sources", "data_sensitivity", "business_unit", "interaction_mode"}}
    keep = redact_for_llm(definition, keep)
    result = await llm.complete_json(RISK_SYSTEM, llm.untrusted("use case", json.dumps(keep, ensure_ascii=False)),
                                     fast=True)
    fm = field_map(definition)
    tier = result.get("eu_ai_act_tier")
    if "eu_ai_act_tier" in fm and tier not in fm["eu_ai_act_tier"].get("options", []):
        tier = None
    return {
        "model": get_settings().fast_model,
        "prompt_version": "risk-v1",
        "risk_tier": tier,
        "risk_rationale": result.get("rationale", ""),
        "risk_categories": normalize_fields(definition, {"risk_categories": result.get("risk_categories")}).get("risk_categories", []),
        "mitigations": [str(m) for m in result.get("mitigations") or []],
        "confidence": float(result.get("confidence") or 0),
    }
