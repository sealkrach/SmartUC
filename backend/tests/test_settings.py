import pytest

from app.services import llm_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_llm_settings_roundtrip_and_key_never_returned(client, admin_headers, requester_headers, monkeypatch):
    r = await client.get("/api/settings/llm", headers=requester_headers)
    assert r.status_code == 200 and r.json()["source"] == "env" and "providers" in r.json()
    # requester cannot change
    r = await client.put("/api/settings/llm", headers=requester_headers,
                         json={"provider": "openai", "model": "gpt-4.1-mini"})
    assert r.status_code == 403
    r = await client.put("/api/settings/llm", headers=admin_headers,
                         json={"provider": "openai", "model": "gpt-4.1-mini", "fast_model": "gpt-4.1-mini",
                               "api_key": "sk-test-1234567890abcdef"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "db" and body["has_key"] and body["key_hint"] == "sk-test…cdef"
    assert "sk-test-1234567890abcdef" not in r.text
    cfg = llm_settings.current()
    assert cfg.model_string() == "openai/gpt-4.1-mini" and cfg.api_key == "sk-test-1234567890abcdef"
    # keep key on update without api_key, clear with ""
    r = await client.put("/api/settings/llm", headers=admin_headers, json={"provider": "openai", "model": "gpt-4.1"})
    assert r.json()["has_key"] is True and r.json()["model"] == "gpt-4.1"
    r = await client.put("/api/settings/llm", headers=admin_headers, json={"provider": "openai", "model": "gpt-4.1", "api_key": ""})
    assert r.json()["has_key"] is False
    # ollama requires api_base
    r = await client.put("/api/settings/llm", headers=admin_headers, json={"provider": "ollama", "model": "llama3.1"})
    assert r.status_code == 400

    # connection test with a scripted provider: latency reported, no provider text leaked
    import litellm

    class Msg:  # minimal response shape
        content = "OK"

    class Choice:
        message = Msg()

    class Resp:
        choices = [Choice()]

    async def fake_ok(**kw):
        assert kw["model"] == "openai/gpt-4.1" and kw.get("api_key") == "sk-live-abcdefghijklmnop"
        return Resp()

    monkeypatch.setattr(litellm, "acompletion", fake_ok)
    r = await client.post("/api/settings/llm/test", headers=admin_headers,
                          json={"provider": "openai", "model": "gpt-4.1", "api_key": "sk-live-abcdefghijklmnop"})
    assert r.status_code == 200 and r.json()["ok"] and r.json()["latency_ms"] >= 0

    async def fake_auth_error(**kw):
        raise RuntimeError("AuthenticationError: 401 invalid x-api-key SECRET-DETAIL")

    monkeypatch.setattr(litellm, "acompletion", fake_auth_error)
    r = await client.post("/api/settings/llm/test", headers=admin_headers)
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["reason"] == "authentication"
    assert "SECRET-DETAIL" not in r.text
    # requester cannot test
    assert (await client.post("/api/settings/llm/test", headers=requester_headers)).status_code == 403
    # audit never contains the key
    r = await client.get("/api/audit?action=settings.llm_update", headers=admin_headers)
    assert r.json() and "sk-test" not in r.text


def test_encryption_roundtrip():
    tok = llm_settings.encrypt("sk-abc")
    assert tok != "sk-abc" and llm_settings.decrypt(tok) == "sk-abc"
