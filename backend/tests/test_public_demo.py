import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_public_demo_guest_is_read_only(client, admin_headers, requester_headers):
    assert (await client.get("/api/demo/public")).json()["enabled"] is False
    assert (await client.post("/api/demo/guest-login")).status_code == 404
    assert (await client.put("/api/demo/public", headers=requester_headers, json={"enabled": True})).status_code == 403
    r = await client.put("/api/demo/public", headers=admin_headers, json={"enabled": True})
    assert r.status_code == 200 and r.json()["public"] is True
    assert (await client.get("/api/demo/public")).json()["enabled"] is True

    r = await client.post("/api/demo/guest-login")
    assert r.status_code == 200
    g = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/api/auth/me", headers=g)).json()
    assert me["is_demo_guest"] and me["role"] == "reviewer"
    # reads work, including reviewer-level pages
    assert (await client.get("/api/usecases?limit=5", headers=g)).status_code == 200
    assert (await client.get("/api/portfolio/relations", headers=g)).status_code == 200
    assert (await client.get("/api/audit", headers=g)).status_code == 200
    # every write is refused
    uid = (await client.get("/api/usecases?limit=1", headers=g)).json()[0]["id"]
    for method, path, body in [("post", "/api/usecases", {"fields": {"title": "x"}}),
                               ("put", f"/api/usecases/{uid}", {"fields": {}}),
                               ("post", f"/api/usecases/{uid}/transition", {"to": "submitted"}),
                               ("post", "/api/users/me/api-keys", {"name": "k"}),
                               ("post", "/api/demo/remove", None),
                               ("put", "/api/settings/llm", {"provider": "openai", "model": "x"})]:
        r = await getattr(client, method)(path, headers=g, json=body)
        assert r.status_code == 403, (path, r.status_code)
    # MCP refuses guest tokens
    r = await client.post("/mcp", headers={**g, "Accept": "application/json, text/event-stream",
                                           "Content-Type": "application/json"},
                          json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert r.status_code == 401
    # logout still allowed
    assert (await client.post("/api/auth/logout", headers=g)).status_code == 204
    # disabling kills existing guest sessions
    r = await client.post("/api/demo/guest-login")
    g2 = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.put("/api/demo/public", headers=admin_headers, json={"enabled": False})
    assert (await client.get("/api/auth/me", headers=g2)).status_code == 401
    assert (await client.post("/api/demo/guest-login")).status_code == 404
