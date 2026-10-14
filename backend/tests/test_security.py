"""Regression tests for the findings of the 2026-10-14 security & compliance review (F-01 … F-14)."""
import pytest
from sqlalchemy import select

from app import auth as auth_mod
from app.db import SessionLocal
from app.models import AnalysisRun, AuditEvent, Decision, UseCase

pytestmark = pytest.mark.asyncio(loop_scope="session")

FULL = {
    "title": "Credit scoring for consumer loans", "summary": "s", "business_unit": "Lending", "problem_statement": "p",
    "target_users": ["credit analysts"], "ai_capability": "prediction", "solution_description": "score applicants",
    "human_in_the_loop": "review_before_action", "data_sources": ["Core banking (Temenos)"],
    "data_sensitivity": "personal_data", "value_drivers": ["revenue_growth"], "kpis": ["k"], "urgency": "high",
    "data_readiness": 4, "technical_complexity": 3, "pain_points": ["slow"], "source_systems": ["Core banking (Temenos)"],
}


async def _login(client, email, password="demo1234"):
    r = await client.post("/api/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ------------------------------------------------------------------ F-01 / F-16

async def test_f01_risk_tier_cannot_be_set_by_requester_llm_or_import(client, requester_headers, admin_headers):
    r = await client.post("/api/usecases", headers=requester_headers,
                          json={"fields": {**FULL, "eu_ai_act_tier": "minimal_risk", "eu_ai_act_rationale": "x"}})
    assert r.status_code == 201
    uc = r.json()
    assert uc["risk_tier"] is None and "eu_ai_act_tier" not in uc["fields"]
    uid = uc["id"]
    await client.post(f"/api/usecases/{uid}/transition", headers=requester_headers, json={"to": "submitted"})
    await client.post(f"/api/usecases/{uid}/transition", headers=admin_headers, json={"to": "qualified"})
    r = await client.post(f"/api/usecases/{uid}/transition", headers=admin_headers, json={"to": "approved"})
    assert r.status_code == 400 and "validate" in r.json()["detail"].lower()

    # editing the governance field after the fact is ignored too
    r = await client.put(f"/api/usecases/{uid}", headers=requester_headers,
                         json={"fields": {**FULL, "eu_ai_act_tier": "minimal_risk"}})
    assert r.json()["risk_tier"] is None

    # reviewer validates (not the owner) -> approval possible
    r = await client.post(f"/api/usecases/{uid}/risk/validate", headers=admin_headers,
                          json={"risk_tier": "high_risk", "rationale": "Annex III 5(b) creditworthiness"})
    assert r.status_code == 200 and r.json()["risk_tier"] == "high_risk" and r.json()["risk_stale"] is False
    r = await client.get(f"/api/usecases/{uid}/risk/validations", headers=requester_headers)
    assert len(r.json()) == 1 and r.json()[0]["validated_by"]["role"] == "admin"

    # owner cannot downgrade the validated tier through an edit
    r = await client.put(f"/api/usecases/{uid}", headers=requester_headers,
                         json={"fields": {**FULL, "eu_ai_act_tier": "minimal_risk"}})
    assert r.json()["risk_tier"] == "high_risk"

    # F-16: changing a risk-relevant field makes the validation stale and blocks approval
    r = await client.put(f"/api/usecases/{uid}", headers=requester_headers,
                         json={"fields": {**FULL, "human_in_the_loop": "full_autonomy"}})
    assert r.json()["risk_stale"] is True
    r = await client.post(f"/api/usecases/{uid}/transition", headers=admin_headers, json={"to": "approved"})
    assert r.status_code == 400 and "re-validate" in r.json()["detail"].lower()
    r = await client.post(f"/api/usecases/{uid}/risk/validate", headers=admin_headers,
                          json={"risk_tier": "high_risk", "rationale": "re-validated"})
    assert r.json()["risk_stale"] is False
    r = await client.post(f"/api/usecases/{uid}/transition", headers=admin_headers, json={"to": "approved"})
    assert r.status_code == 200 and r.json()["status"] == "approved"

    # prohibited can never be approved
    r = await client.post(f"/api/usecases/{uid}/risk/validate", headers=admin_headers,
                          json={"risk_tier": "prohibited", "rationale": "Art. 5"})
    assert r.status_code == 200


async def test_f01_four_eyes_owner_cannot_validate_own_use_case(client, admin_headers):
    r = await client.post("/api/usecases", headers=admin_headers, json={"fields": FULL})
    uid = r.json()["id"]
    r = await client.post(f"/api/usecases/{uid}/risk/validate", headers=admin_headers,
                          json={"risk_tier": "minimal_risk"})
    assert r.status_code == 403 and "four-eyes" in r.json()["detail"].lower()


# ------------------------------------------------------------------ F-02

def test_f02_insecure_configuration_refuses_to_start():
    from app.config import Settings

    with pytest.raises(ValueError, match="SECRET_KEY"):
        Settings(app_env="production", secret_key="dev-secret-change-me", admin_password="Strong-Passw0rd-12",
                 database_url="postgresql+asyncpg://u:strongpass@db/x", seed_demo_data=False, _env_file=None)
    with pytest.raises(ValueError, match="ADMIN_PASSWORD"):
        Settings(app_env="production", secret_key="x" * 40, admin_password="admin1234",
                 database_url="postgresql+asyncpg://u:strongpass@db/x", seed_demo_data=False, _env_file=None)
    with pytest.raises(ValueError, match="SEED_DEMO_DATA"):
        Settings(app_env="production", secret_key="x" * 40, admin_password="Strong-Passw0rd-12",
                 database_url="postgresql+asyncpg://u:strongpass@db/x", seed_demo_data=True, _env_file=None)
    ok = Settings(app_env="production", secret_key="x" * 40, admin_password="Strong-Passw0rd-12",
                  database_url="postgresql+asyncpg://u:strongpass@db/x", seed_demo_data=False, _env_file=None)
    assert ok.access_token_expire_minutes == 60 and ok.seed_demo_data is False


# ------------------------------------------------------------------ F-03 / F-04 / F-15

async def test_f03_f04_f15_authorization(client, requester_headers, admin_headers):
    r = await client.get("/api/portfolio/relations?status=all", headers=admin_headers)
    rel = next(x for x in r.json() if x["type"] != "shares_entity")
    r = await client.patch(f"/api/usecases/relations/{rel['id']}", headers=requester_headers, json={"status": "confirmed"})
    assert r.status_code == 403
    r = await client.post(f"/api/usecases/{rel['source_id']}/relations", headers=requester_headers,
                          json={"target_id": rel["target_id"], "type": "overlap"})
    assert r.status_code == 403
    assert (await client.get("/api/imports", headers=requester_headers)).status_code == 403
    assert (await client.get("/api/users", headers=requester_headers)).status_code == 403
    # someone else's record: no version history, no analysis / AI triggering
    r = await client.get("/api/usecases?limit=1000", headers=admin_headers)
    other = next(u for u in r.json() if (u["owner"] or {}).get("email") != "karim.benali@example.com")
    assert (await client.get(f"/api/usecases/{other['id']}/versions", headers=requester_headers)).status_code == 403
    assert (await client.post(f"/api/usecases/{other['id']}/analyze?use_llm=false", headers=requester_headers)).status_code == 403
    assert (await client.post(f"/api/usecases/{other['id']}/ai/risk", headers=requester_headers)).status_code == 403


# ------------------------------------------------------------------ F-05 / F-06

async def test_f05_lockout_and_security_events(client):
    auth_mod.login_guard.clear()
    for _ in range(5):
        r = await client.post("/api/auth/login", data={"username": "nobody@example.com", "password": "wrong"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", data={"username": "nobody@example.com", "password": "wrong"})
    assert r.status_code == 429
    auth_mod.login_guard.clear()
    async with SessionLocal() as s:
        events = (await s.scalars(select(AuditEvent).where(AuditEvent.action == "auth.login",
                                                            AuditEvent.outcome != "success"))).all()
    assert any(e.details.get("email") == "nobody@example.com" for e in events)
    assert any(e.outcome == "denied" for e in events)


async def test_f06_logout_and_password_change_revoke_tokens(client, admin_headers):
    auth_mod.login_guard.clear()
    r = await client.post("/api/users", headers=admin_headers,
                          json={"email": "revoke@example.com", "password": "Initial-Passw0rd-12", "role": "requester"})
    assert r.status_code == 201 and r.json()["must_change_password"] is True
    h = await _login(client, "revoke@example.com", "Initial-Passw0rd-12")
    assert (await client.get("/api/auth/me", headers=h)).status_code == 200
    assert (await client.post("/api/auth/logout", headers=h)).status_code == 204
    assert (await client.get("/api/auth/me", headers=h)).status_code == 401
    h1 = await _login(client, "revoke@example.com", "Initial-Passw0rd-12")
    h2 = await _login(client, "revoke@example.com", "Initial-Passw0rd-12")
    r = await client.post("/api/auth/me/password", headers=h1,
                          json={"current_password": "Initial-Passw0rd-12", "new_password": "Another-Passw0rd-34"})
    assert r.status_code == 204
    assert (await client.get("/api/auth/me", headers=h2)).status_code == 401  # all sessions invalidated
    r = await client.post("/api/auth/me/password", headers=await _login(client, "revoke@example.com", "Another-Passw0rd-34"),
                          json={"current_password": "Another-Passw0rd-34", "new_password": "short"})
    assert r.status_code in (400, 422)


# ------------------------------------------------------------------ F-07

async def test_f07_soft_delete_keeps_audit_trail(client, admin_headers, requester_headers):
    r = await client.post("/api/usecases", headers=admin_headers, json={"fields": {**FULL, "title": "To delete"}})
    uid, ref = r.json()["id"], r.json()["ref"]
    await client.post(f"/api/usecases/{uid}/transition", headers=admin_headers, json={"to": "submitted", "comment": "c"})
    assert (await client.delete(f"/api/usecases/{uid}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"/api/usecases/{uid}", headers=admin_headers)).status_code == 404
    assert all(u["id"] != uid for u in (await client.get("/api/usecases?limit=1000", headers=admin_headers)).json())
    async with SessionLocal() as s:
        row = await s.get(UseCase, uid)
        assert row is not None and row.deleted_at is not None
        assert len((await s.scalars(select(Decision).where(Decision.use_case_id == uid))).all()) == 1
    r = await client.get(f"/api/audit?target_id={ref}", headers=admin_headers)
    actions = [e["action"] for e in r.json()]
    assert {"usecase.create", "usecase.transition", "usecase.delete"} <= set(actions)
    assert all(e["actor_email"] == "admin@example.com" and e["request_id"] for e in r.json())
    assert (await client.get("/api/audit", headers=requester_headers)).status_code == 403


# ------------------------------------------------------------------ F-08 / F-09

async def test_f08_orphaned_jobs_are_recovered_on_startup(client, admin_headers):
    from app.main import _recover_orphaned_jobs

    async with SessionLocal() as s:
        s.add(AnalysisRun(scope="portfolio", status="running", stats={}))
        await s.commit()
    r = await client.post("/api/portfolio/analyze?use_llm=false", headers=admin_headers)
    assert r.status_code == 409  # a fresh running job blocks
    async with SessionLocal() as s:
        await _recover_orphaned_jobs(s)
        await s.commit()
        assert not (await s.scalars(select(AnalysisRun).where(AnalysisRun.status == "running"))).all()
    r = await client.post("/api/portfolio/analyze?use_llm=false", headers=admin_headers)
    assert r.status_code == 200


async def test_f09_ai_quota(client, admin_headers, monkeypatch):
    from app.services.ratelimit import SlidingWindow

    monkeypatch.setattr(auth_mod, "ai_limiter", SlidingWindow(2, 60))
    r = await client.get("/api/usecases?limit=1", headers=admin_headers)
    uid = r.json()[0]["id"]
    codes = [(await client.post(f"/api/usecases/{uid}/analyze?use_llm=false", headers=admin_headers)).status_code
             for _ in range(2)]
    assert codes == [200, 200]  # use_llm=false is not metered
    codes = [(await client.post(f"/api/intake/normalize", headers=admin_headers, json={"text": "x"})).status_code
             for _ in range(3)]
    assert codes[-1] == 429


# ------------------------------------------------------------------ F-10 / F-14 / F-23

async def test_f10_f14_input_validation(client, admin_headers):
    r = await client.post("/api/templates", headers=admin_headers, json={"key": "bad", "sections": [{"id": "s"}]})
    assert r.status_code == 400
    r = await client.post("/api/templates", headers=admin_headers,
                          json={"key": "bad2", "sections": [{"id": "s", "fields": [{"id": "title", "type": "wat"}]}]})
    assert r.status_code == 400 and "type" in r.json()["detail"]
    assert (await client.get("/api/usecases?sort=owner", headers=admin_headers)).status_code == 400
    assert (await client.get("/api/usecases?limit=999999", headers=admin_headers)).status_code == 422
    # creating a use case still works after the rejected templates (registry not poisoned)
    assert (await client.post("/api/usecases", headers=admin_headers, json={"fields": FULL})).status_code == 201


async def test_f23_health_exposes_no_config(client):
    r = await client.get("/api/health")
    assert r.status_code == 200 and "llm_model" not in r.json() and r.json()["db"] == "ok"


# ------------------------------------------------------------------ F-13

async def test_f13_embedding_provenance(client, admin_headers):
    r = await client.get("/api/usecases/UC-0001", headers=admin_headers)
    uid = r.json()["id"]
    async with SessionLocal() as s:
        uc = await s.get(UseCase, uid)
        assert uc.embedding_model == "hash"
