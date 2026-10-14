"""Intake agent + normalizer plumbing with a scripted LLM (no API key needed)."""
import json

import pytest

from app.services import llm

pytestmark = pytest.mark.asyncio(loop_scope="session")

FULL = {
    "title": "Invoice triage", "summary": "Triage supplier invoices", "business_unit": "Finance",
    "problem_statement": "Manual invoice handling", "target_users": ["AP clerks"], "ai_capability": "extraction",
    "solution_description": "Extract and route", "human_in_the_loop": "review_before_action",
    "data_sources": ["Supplier invoices"], "data_sensitivity": "confidential", "value_drivers": ["cost_reduction"],
    "kpis": ["Handling time -60%"], "urgency": "high", "source_systems": ["SAP S/4HANA"], "estimated_annual_value": 500000,
    "data_readiness": 4, "technical_complexity": 2, "current_process": "x", "pain_points": ["slow"],
    "risk_categories": ["privacy"], "teams_involved": ["Finance IT"], "estimated_effort": "M", "affected_population": 45,
    "change_impact": 2, "target_systems": ["SAP S/4HANA"], "interaction_mode": "embedded_in_app",
}


def scripted_llm(monkeypatch):
    calls = []

    async def fake(system: str, user: str, **kw):
        calls.append(system[:40])
        if "intake assistant of an AI use case registry" in system:  # extract
            if "FULL" in user:
                return {"fields": FULL, "assumptions": ["urgency inferred as high"], "user_wants_to_finish": False}
            return {"fields": {"title": "Invoice triage", "problem_statement": "Manual invoice handling"},
                    "assumptions": [], "user_wants_to_finish": False}
        if "friendly, sharp intake assistant" in system:  # ask
            return {"message": "Got it. Who are the users and which systems are involved?", "fields_asked": ["target_users"]}
        if "draft use case is now complete" in system:  # wrap up
            return {"message": "All set — review the canvas and create the use case."}
        if "AI portfolio analyst" in system:  # normalize
            return {"fields": FULL, "assumptions": [], "open_questions": ["Which SAP module?"]}
        if "EU AI Act compliance analyst" in system:
            return {"eu_ai_act_tier": "minimal_risk", "rationale": "Internal back-office automation.",
                    "risk_categories": ["hallucination_accuracy"], "mitigations": ["Human review"], "confidence": 0.8}
        if "expert editor" in system:
            return {"value": "Manual invoice handling costs 12 minutes per invoice.", "notes": "Added the measurable pain."}
        if "senior AI portfolio architect" in system:  # judge
            raw = user.split("CANDIDATES B:")[1]
            data = json.loads(raw[raw.index("[") : raw.rindex("]") + 1])
            return {"results": [{"id": c["id"], "type": "duplicate", "confidence": 0.9,
                                 "explanation": "Same AP invoice problem.", "suggested_action": "Merge."} for c in data[:1]]}
        raise AssertionError(f"unexpected prompt: {system[:80]}")

    monkeypatch.setattr(llm, "complete_json", fake)
    return calls


async def test_intake_interview_to_usecase(client, requester_headers, monkeypatch):
    calls = scripted_llm(monkeypatch)
    r = await client.post("/api/intake", headers=requester_headers, json={})
    assert r.status_code == 201
    it = r.json()
    assert it["phase"] == "interview" and it["messages"][0]["role"] == "assistant"

    r = await client.post(f"/api/intake/{it['id']}/messages", headers=requester_headers,
                          json={"content": "We want to triage invoices automatically"})
    it = r.json()
    assert it["draft"]["title"] == "Invoice triage"
    assert it["missing_required"] and it["phase"] == "interview"
    assert it["messages"][-1]["content"].startswith("Got it")

    r = await client.post(f"/api/intake/{it['id']}/messages", headers=requester_headers, json={"content": "FULL details"})
    it = r.json()
    assert it["missing_required"] == [] and it["phase"] == "ready"
    assert it["assumptions"] == ["urgency inferred as high"]
    assert "All set" in it["messages"][-1]["content"]

    # manual override from the live canvas
    r = await client.put(f"/api/intake/{it['id']}/draft", headers=requester_headers, json={"draft": {"urgency": "medium"}})
    assert r.json()["draft"]["urgency"] == "medium"

    r = await client.post(f"/api/intake/{it['id']}/convert?submit=true", headers=requester_headers)
    assert r.status_code == 200
    uc_id = r.json()["use_case_id"]
    r = await client.get(f"/api/usecases/{uc_id}", headers=requester_headers)
    uc = r.json()
    assert uc["status"] == "submitted" and uc["source"] == "intake" and uc["priority_score"] is not None
    assert uc["ai_suggestions"]["assumptions"] == ["urgency inferred as high"]

    # AI helpers
    r = await client.post(f"/api/usecases/{uc_id}/ai/improve", headers=requester_headers,
                          json={"field_id": "problem_statement"})
    assert "12 minutes" in r.json()["value"]
    r = await client.post(f"/api/usecases/{uc_id}/ai/risk", headers=requester_headers)
    assert r.json()["risk_tier"] == "minimal_risk"

    # LLM-judged overlap against the seeded invoice use cases
    r = await client.post(f"/api/usecases/{uc_id}/analyze", headers=requester_headers)
    assert r.json()["candidates"] >= 1 and r.json()["created"] >= 1
    r = await client.get(f"/api/usecases/{uc_id}/relations", headers=requester_headers)
    assert any(x["type"] == "duplicate" and x["status"] == "suggested" for x in r.json())
    assert calls


async def test_normalize_endpoint(client, requester_headers, monkeypatch):
    scripted_llm(monkeypatch)
    r = await client.post("/api/intake/normalize", headers=requester_headers, json={"text": "some brief"})
    assert r.status_code == 200
    assert r.json()["fields"]["ai_capability"] == "extraction" and r.json()["open_questions"]


async def test_llm_unavailable_is_503(client, requester_headers, monkeypatch):
    async def boom(*a, **k):
        raise llm.LLMUnavailable("no key")
    monkeypatch.setattr(llm, "complete_json", boom)
    r = await client.post("/api/intake/normalize", headers=requester_headers, json={"text": "x"})
    assert r.status_code == 503
