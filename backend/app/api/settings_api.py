"""Application settings: LLM provider / model / API key, with a connection + latency check."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import check_ai_quota, get_current_user, require_role
from ..db import get_session
from ..models import Role, User
from ..services import audit, llm_settings

router = APIRouter(prefix="/settings", tags=["settings"])


class LLMSettingsIn(BaseModel):
    provider: str = Field(max_length=30)
    model: str = Field(min_length=1, max_length=120)
    fast_model: str = Field(default="", max_length=120)
    api_base: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, max_length=500)  # None = keep, "" = clear


class LLMTestIn(BaseModel):
    """Test with unsaved values (admins); an empty body / no provider uses the stored configuration."""
    provider: str = Field(default="", max_length=30)
    model: str = Field(default="", max_length=120)
    fast_model: str = Field(default="", max_length=120)
    api_base: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    fast: bool = False


class LLMModelsIn(BaseModel):
    """Candidate connection values for model discovery; empty provider = stored configuration."""
    provider: str = Field(default="", max_length=30)
    api_base: str = Field(default="", max_length=300)
    api_key: str | None = Field(default=None, max_length=500)


def _candidate_config(provider: str, api_base: str, api_key: str | None, model: str = "x") -> llm_settings.LLMConfig:
    """Build an unsaved LLMConfig; without an explicit key, reuse the stored key when the provider matches."""
    stored = llm_settings.current()
    data = {"provider": provider, "model": model, "fast_model": model, "api_base": api_base}
    cfg = llm_settings.LLMConfig({**data, "api_key_enc": llm_settings.encrypt(api_key)} if api_key else data)
    if not api_key and stored.api_key and stored.provider == provider:
        cfg.api_key = stored.api_key
    return cfg


@router.get("/llm")
async def get_llm(_: User = Depends(get_current_user)):
    return llm_settings.current().public()


@router.put("/llm")
async def put_llm(payload: LLMSettingsIn, session: AsyncSession = Depends(get_session),
                  admin: User = Depends(require_role(Role.admin))):
    if payload.provider not in llm_settings.PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    if llm_settings.PROVIDERS[payload.provider]["needs_base"] and not payload.api_base:
        raise HTTPException(400, "This provider needs an API base URL")
    if payload.api_base and not payload.api_base.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "API base URL must start with http:// or https://")
    if not payload.model.strip():
        raise HTTPException(400, "A model name is required")
    try:
        cfg = await llm_settings.save(session, provider=payload.provider, model=payload.model,
                                      fast_model=payload.fast_model, api_base=payload.api_base,
                                      api_key=payload.api_key, updated_by=admin.email)
    except ValueError as e:
        raise HTTPException(400, str(e))
    await audit.record(session, admin, "settings.llm_update", target_type="settings", target_id="llm",
                       details={"provider": cfg.provider, "model": cfg.model, "fast_model": cfg.fast_model,
                                "api_base": cfg.api_base,
                                "api_key": "kept" if payload.api_key is None else ("cleared" if payload.api_key == "" else "replaced")})
    await session.commit()
    return cfg.public()


@router.post("/llm/test")
async def test_llm(payload: LLMTestIn | None = None, session: AsyncSession = Depends(get_session),
                   user: User = Depends(require_role(Role.reviewer))):
    """Round-trip to the provider with a 5-token prompt; returns latency. Uses stored settings unless a body
    with candidate values is given (admins trying a configuration before saving)."""
    check_ai_quota(user)
    stored = llm_settings.current()
    if payload and payload.provider and user.role == Role.admin.value:
        if payload.provider not in llm_settings.PROVIDERS:
            raise HTTPException(400, "Unknown provider")
        if not payload.model.strip():
            raise HTTPException(400, "A model name is required")
        cfg = _candidate_config(payload.provider, payload.api_base, payload.api_key, payload.model)
        cfg.fast_model = payload.fast_model.strip() or cfg.model
        fast = payload.fast
    else:
        cfg, fast = stored, bool(payload and payload.fast)
    result = await llm_settings.test_connection(cfg, fast=fast)
    if not payload or (cfg.provider == stored.provider and cfg.model == stored.model):
        await llm_settings.record_check(session, result)
    await audit.record(session, user, "settings.llm_test", target_type="settings", target_id="llm",
                       details={"model": result["model"], "ok": result["ok"], "latency_ms": result["latency_ms"],
                                "reason": result.get("reason")}, outcome="success" if result["ok"] else "failure")
    await session.commit()
    return result


@router.post("/llm/models")
async def list_llm_models(payload: LLMModelsIn | None = None, session: AsyncSession = Depends(get_session),
                          user: User = Depends(require_role(Role.reviewer))):
    """Models available to the configured key (or to the candidate values an admin is about to save)."""
    check_ai_quota(user)
    if payload and payload.provider and user.role == Role.admin.value:
        if payload.provider not in llm_settings.PROVIDERS:
            raise HTTPException(400, "Unknown provider")
        if payload.api_base and not payload.api_base.lower().startswith(("http://", "https://")):
            raise HTTPException(400, "API base URL must start with http:// or https://")
        cfg = _candidate_config(payload.provider, payload.api_base, payload.api_key)
    else:
        cfg = llm_settings.current()
    result = await llm_settings.list_models(cfg)
    await audit.record(session, user, "settings.llm_list_models", target_type="settings", target_id="llm",
                       details={"provider": cfg.provider, "ok": result["ok"], "count": len(result["models"]),
                                "reason": result["reason"]})
    await session.commit()
    return result
