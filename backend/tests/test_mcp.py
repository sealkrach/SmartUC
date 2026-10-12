"""MCP interface: API keys, transport gate, and tool implementations."""
import pytest

from app import mcp_tools as T
from app.models import User

pytestmark = pytest.mark.asyncio(loop_scope="session")

MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


async def _api_key(client, headers) -> str:
    r = await client.post("/api/users/me/api-keys", headers=headers, json={"name": "test"})
    assert r.status_code == 201 and r.json()["key"].startswith("uch_")
    return r.json()["key"]


async def test_api_key_lifecycle(client, admin_headers):
    key = await _api_key(client, admin_headers)
    # the key authenticates REST calls too
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["role"] == "admin"
    r = await client.get("/api/users/me/api-keys", headers=admin_headers)
    kid = [k for k in r.json() if k["prefix"] == key[:10]][0]["id"]
    r = await client.delete(f"/api/users/me/api-keys/{kid}", headers=admin_headers)
    assert r.status_code == 204
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 401


async def test_mcp_transport_requires_key_and_lists_tools(client, admin_headers):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "0"}}}
    r = await client.post("/mcp", json=init, headers=MCP_HEADERS)
    assert r.status_code == 401
    key = await _api_key(client, admin_headers)
    auth = {**MCP_HEADERS, "Authorization": f"Bearer {key}"}
    r = await client.post("/mcp", json=init, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["serverInfo"]["name"] == "UseCase Hub"
    auth["Mcp-Session-Id"] = r.headers["mcp-session-id"]
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=auth)
    assert r.status_code in (200, 202)
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=auth)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"list_use_cases", "get_use_case", "search_similar", "create_use_case", "transition_use_case",
            "start_intake", "list_relations", "review_relation", "analyze_portfolio", "get_template"} <= names
    # call a tool end-to-end through the protocol
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                        "params": {"name": "list_use_cases", "arguments": {"limit": 3}}}, headers=auth)
    res = r.json()["result"]
    assert not res.get("isError"), res
    assert "UC-" in res["content"][0]["text"]
    # bad call -> readable error, not a crash
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                        "params": {"name": "get_use_case", "arguments": {"ref": "UC-9999"}}}, headers=auth)
    res = r.json()["result"]
    assert res.get("isError") and "404" in res["content"][0]["text"]
    r = await client.delete("/mcp", headers=auth)  # terminate the session
    assert r.status_code in (200, 204)


async def test_mcp_tools_registry_flow(client, admin_headers, requester_headers):
    from sqlalchemy import select

    from app.db import SessionLocal

    async with SessionLocal() as s:
        admin = await s.scalar(select(User).where(User.role == "admin"))
        requester = await s.scalar(select(User).where(User.email == "karim.benali@example.com"))

    tpl = await T.get_template(admin)
    assert "title" in tpl["fields_schema"] and tpl["key"]

    hits = await T.search_similar(admin, "classify supplier invoices and pre-fill SAP accounts payable")
    assert hits and "invoice" in hits[0]["title"].lower()

    created = await T.create_use_case(requester, {
        "title": "MCP created use case", "summary": "s", "business_unit": "QA", "problem_statement": "p",
        "target_users": ["tester"], "ai_capability": "classification", "solution_description": "d",
        "human_in_the_loop": "none", "data_sources": ["ServiceNow incident tickets"], "data_sensitivity": "internal",
        "value_drivers": ["quality"], "kpis": ["k"], "urgency": "low", "source_systems": ["ServiceNow"],
        "data_readiness": 3, "technical_complexity": 2, "pain_points": ["x"]}, submit=True)
    assert created["status"] == "submitted" and created["source"] == "manual"
    ref = created["ref"]

    upd = await T.update_use_case(requester, ref, {"estimated_annual_value": 120000, "kpis": None})
    assert upd["fields"]["estimated_annual_value"] == 120000 and "kpis" not in upd["fields"]

    with pytest.raises(T.ToolError, match="403"):
        await T.validate_risk(requester, ref, "minimal_risk")
    with pytest.raises(T.ToolError, match="400"):
        await T.transition_use_case(requester, ref, "qualified")

    assert (await T.transition_use_case(admin, ref, "qualified", "ok"))["status"] == "qualified"
    assert (await T.validate_risk(admin, ref, "minimal_risk", "internal"))["risk_tier"] == "minimal_risk"
    hist = await T.get_history(admin, ref)
    assert len(hist["decisions"]) == 2 and hist["versions"][0]["version"] >= 3

    stats = await T.analyze_use_case(admin, ref, use_llm=False)
    assert stats["structural_relations"] >= 1
    rels = await T.list_relations(admin, ref=ref, status="all", include_structural=True)
    assert any(r["type"] == "shares_entity" for r in rels)

    other = (await T.list_use_cases(admin, q="ServiceNow", limit=1))[0]
    rel = await T.add_relation(admin, ref, other["ref"], "complementary", "same ticket data")
    assert rel["status"] == "confirmed"
    dash = await T.dashboard(admin)
    assert dash["total"] >= 27
    ents = await T.list_entities(admin, kind="system")
    assert any(e["name"] == "ServiceNow" for e in ents)
