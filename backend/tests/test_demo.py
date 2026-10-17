import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_demo_mode_load_and_remove(client, admin_headers, requester_headers):
    # dev seed already loaded the demo portfolio: status reflects it
    r = await client.get("/api/demo", headers=requester_headers)
    assert r.status_code == 200 and r.json()["active"] and r.json()["count"] >= 20
    assert (await client.post("/api/demo/load", headers=requester_headers)).status_code == 403
    assert (await client.post("/api/demo/load", headers=admin_headers)).status_code == 409
    before = len((await client.get("/api/usecases?limit=1000", headers=admin_headers)).json())
    r = await client.post("/api/demo/remove", headers=admin_headers)
    assert r.status_code == 200 and r.json()["active"] is False
    after = (await client.get("/api/usecases?limit=1000", headers=admin_headers)).json()
    assert len(after) < before and all("demo" not in u["tags"] for u in after)
    # reload in "production style": inactive technical owner, validator = admin
    r = await client.post("/api/demo/load", headers=admin_headers)
    assert r.status_code == 200 and r.json()["count"] == r.json()["available"] and r.json()["loaded_by"] == "admin@example.com"
    ucs = (await client.get("/api/usecases?limit=1000", headers=admin_headers)).json()
    demo = [u for u in ucs if "demo" in u["tags"]]
    assert len(demo) == r.json()["count"] and all(u["owner"]["email"] == "demo-owner@smartuc.local" for u in demo)
    assert any(u["risk_tier"] == "high_risk" for u in demo)  # validated via four-eyes by the admin
    r = await client.get("/api/portfolio/relations?status=all", headers=admin_headers)
    assert any(x["type"] == "overlap" for x in r.json())
    r = await client.get("/api/audit?action=demo.load", headers=admin_headers)
    assert r.json()
