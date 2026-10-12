import pytest

from app.services import templates as T
from app.services.workflow import allowed_transitions, can_transition

pytestmark = pytest.mark.anyio


# ----------------------------------------------------------------- pure logic

def test_completeness_and_scores():
    d = T.load_yaml_templates()[0]
    fields = T.normalize_fields(d, {
        "title": "X", "summary": "Y", "business_unit": "Ops", "problem_statement": "P",
        "target_users": "analysts, clerks", "ai_capability": "Extraction", "solution_description": "S",
        "human_in_the_loop": "review before action", "data_sources": ["A"], "data_sensitivity": "internal",
        "value_drivers": ["cost_reduction", "bogus"], "kpis": ["k"], "urgency": "high",
        "estimated_annual_value": "1 000 000", "data_readiness": 9, "technical_complexity": 2,
    })
    assert fields["target_users"] == ["analysts", "clerks"]
    assert fields["ai_capability"] == "extraction"
    assert fields["human_in_the_loop"] == "review_before_action"
    assert fields["value_drivers"] == ["cost_reduction"]
    assert fields["estimated_annual_value"] == 1000000
    assert fields["data_readiness"] == 5  # clamped
    score, missing = T.completeness(d, fields)
    assert 0 < score < 1 and missing == []
    scores = T.compute_scores(d, fields)
    assert scores["value_score"] is not None and scores["feasibility_score"] is not None
    assert scores["priority_score"] is not None


def test_workflow_gates():
    assert can_transition("draft", "submitted", "requester")[0]
    assert not can_transition("submitted", "qualified", "requester")[0]
    assert can_transition("submitted", "qualified", "reviewer")[0]
    assert not can_transition("draft", "approved", "admin")[0]
    tos = {t["to"] for t in allowed_transitions("qualified", "reviewer")}
    assert tos == {"approved", "rejected", "draft"}


def test_hash_embedding_similarity():
    from app.services.llm import cosine, hash_embedding

    a = hash_embedding("Automated triage of supplier invoices into SAP accounts payable", 384)
    b = hash_embedding("Invoice processing assistant for accounts payable posting to SAP", 384)
    c = hash_embedding("Branch cash demand forecasting for ATMs", 384)
    assert cosine(a, b) > cosine(a, c)


# ----------------------------------------------------------------- API

async def test_health_and_seed(client, admin_headers):
    r = await client.get("/api/health")
    assert r.status_code == 200
    r = await client.get("/api/usecases", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 20
    assert all(i["ref"].startswith("UC-") for i in items)


async def test_dashboard_graph_relations(client, admin_headers):
    r = await client.get("/api/portfolio/dashboard", headers=admin_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["total"] >= 20 and "by_status" in d and d["matrix"]
    r = await client.get("/api/portfolio/graph", headers=admin_headers)
    g = r.json()
    assert any(n["kind"] == "entity" for n in g["nodes"])
    assert any(e["type"] == "uses_entity" for e in g["edges"])
    # structural relations exist (shared SAP / ServiceNow / Salesforce ...)
    r = await client.get("/api/portfolio/relations?status=all", headers=admin_headers)
    rels = r.json()
    assert any(x["type"] == "shares_entity" for x in rels)
    assert any(x["type"] == "overlap" for x in rels), "lexical overlap candidates expected without LLM"


async def test_create_update_transition(client, requester_headers, admin_headers):
    r = await client.post("/api/usecases", headers=requester_headers, json={"fields": {
        "title": "Test UC", "summary": "s", "business_unit": "QA", "problem_statement": "p",
        "target_users": ["tester"], "ai_capability": "classification", "solution_description": "d",
        "human_in_the_loop": "none", "data_sources": ["ServiceNow incident tickets"], "data_sensitivity": "internal",
        "value_drivers": ["quality"], "kpis": ["k"], "urgency": "low", "data_readiness": 3,
        "technical_complexity": 2, "source_systems": ["ServiceNow"]}})
    assert r.status_code == 201, r.text
    uc = r.json()
    assert uc["status"] == "draft" and uc["missing_required"] == []
    assert {t["to"] for t in uc["allowed_transitions"]} == {"submitted"}

    # requester cannot qualify
    r = await client.post(f"/api/usecases/{uc['id']}/transition", headers=requester_headers, json={"to": "qualified"})
    assert r.status_code == 400
    r = await client.post(f"/api/usecases/{uc['id']}/transition", headers=requester_headers,
                          json={"to": "submitted", "comment": "ready"})
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    r = await client.post(f"/api/usecases/{uc['id']}/transition", headers=admin_headers, json={"to": "qualified"})
    assert r.status_code == 200
    # approval requires validated risk tier
    r = await client.post(f"/api/usecases/{uc['id']}/transition", headers=admin_headers, json={"to": "approved"})
    assert r.status_code == 400
    r = await client.post(f"/api/usecases/{uc['id']}/risk/validate", headers=admin_headers,
                          json={"risk_tier": "minimal_risk", "rationale": "internal tooling"})
    assert r.status_code == 200 and r.json()["risk_tier"] == "minimal_risk"
    r = await client.post(f"/api/usecases/{uc['id']}/transition", headers=admin_headers, json={"to": "approved"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = await client.get(f"/api/usecases/{uc['id']}/decisions", headers=admin_headers)
    assert len(r.json()) == 3

    # analysis without LLM creates structural relations through shared ServiceNow entity
    r = await client.post(f"/api/usecases/{uc['id']}/analyze?use_llm=false", headers=admin_headers)
    assert r.status_code == 200 and r.json()["structural_relations"] >= 1
    r = await client.get(f"/api/usecases/{uc['id']}/entities", headers=admin_headers)
    ents = r.json()
    assert any(e["entity"]["name"] == "ServiceNow" and e["shared_with"] >= 1 for e in ents)

    # relation review
    r = await client.get(f"/api/usecases/{uc['id']}/relations", headers=admin_headers)
    rel = r.json()[0]
    r = await client.patch(f"/api/usecases/relations/{rel['id']}", headers=admin_headers, json={"status": "dismissed"})
    assert r.status_code == 200 and r.json()["status"] == "dismissed"

    # versions
    r = await client.put(f"/api/usecases/{uc['id']}", headers=admin_headers,
                         json={"fields": {**uc["fields"], "title": "Test UC v2"}, "change_note": "rename"})
    assert r.status_code == 200 and r.json()["title"] == "Test UC v2"
    r = await client.get(f"/api/usecases/{uc['id']}/versions", headers=admin_headers)
    assert r.json()[0]["version"] >= 3


async def test_templates_and_users(client, admin_headers, requester_headers):
    r = await client.get("/api/templates", headers=requester_headers)
    assert r.status_code == 200 and r.json()[0]["is_default"]
    tid = r.json()[0]["id"]
    r = await client.get(f"/api/templates/{tid}/yaml", headers=requester_headers)
    assert "sections:" in r.text
    r = await client.post("/api/users", headers=requester_headers,
                          json={"email": "x@example.com", "password": "secret12"})
    assert r.status_code == 403
    r = await client.post("/api/users", headers=admin_headers,
                          json={"email": "x@example.com", "password": "secret12", "role": "reviewer"})
    assert r.status_code == 201


async def test_import_csv_without_llm(client, admin_headers, tmp_path, monkeypatch):
    from app.services import llm

    async def boom(*a, **k):
        raise llm.LLMUnavailable("no key in tests")

    monkeypatch.setattr(llm, "complete_json", boom)
    csv = ("Use case name;Description;Department;Systems\n"
           "Chat for HR;Answer HR questions;HR;SharePoint, Workday\n"
           "Fraud scoring;Score card transactions;Risk;Temenos\n")
    r = await client.post("/api/imports", headers=admin_headers,
                          files={"file": ("ucs.csv", csv.encode(), "text/csv")})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["row_count"] == 2 and job["columns"][0] == "Use case name"
    r = await client.put(f"/api/imports/{job['id']}/mapping", headers=admin_headers, json={
        "mapping": {"Use case name": "title", "Description": "summary", "Department": "business_unit",
                    "Systems": "source_systems"}, "ai_enrich": False})
    assert r.status_code == 200
    r = await client.post(f"/api/imports/{job['id']}/run", headers=admin_headers)
    assert r.status_code == 200
    # background task runs after response within the ASGI transport; poll
    for _ in range(20):
        r = await client.get(f"/api/imports/{job['id']}", headers=admin_headers)
        if r.json()["status"] in ("done", "failed"):
            break
    j = r.json()
    assert j["status"] == "done", j
    assert len(j["created_ids"]) == 2 and j["errors"] == []
    r = await client.get(f"/api/usecases/{j['created_ids'][0]}", headers=admin_headers)
    assert r.json()["fields"]["source_systems"] == ["SharePoint", "Workday"]
    assert r.json()["status"] == "submitted"
