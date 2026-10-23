"""Runtime LLM configuration: provider, models and API key managed from the UI (admin only).

Stored in the `app_settings` table; the API key is encrypted at rest with a key derived from SECRET_KEY
and is never returned to clients (only a hint such as "sk-ant-…3f9a"). Values from the database override
the environment; when nothing is stored, the .env configuration applies.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import AppSetting

log = logging.getLogger(__name__)

SETTING_KEY = "llm"

PROVIDERS: dict[str, dict] = {
    "anthropic": {"label": "Anthropic", "env_key": "ANTHROPIC_API_KEY", "needs_base": False,
                  "models": ["claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-1"]},
    "openai": {"label": "OpenAI", "env_key": "OPENAI_API_KEY", "needs_base": False,
               "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini", "o4-mini"]},
    # Any server speaking the OpenAI chat-completions API: vLLM, LM Studio, LocalAI, OpenRouter, Together,
    # Groq, Mistral/DeepSeek endpoints, a LiteLLM/Portkey proxy, an enterprise gateway... Model name is free text.
    "openai_compatible": {"label": "OpenAI-compatible endpoint (vLLM, OpenRouter, LiteLLM proxy…)",
                          "env_key": "OPENAI_COMPATIBLE_API_KEY", "needs_base": True, "litellm_prefix": "openai",
                          "models": []},
    "mistral": {"label": "Mistral", "env_key": "MISTRAL_API_KEY", "needs_base": False,
                "models": ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest"]},
    "azure": {"label": "Azure OpenAI", "env_key": "AZURE_API_KEY", "needs_base": True,
              "models": ["<deployment-name>"]},
    "gemini": {"label": "Google Gemini", "env_key": "GEMINI_API_KEY", "needs_base": False,
               "models": ["gemini-2.5-pro", "gemini-2.5-flash"]},
    "ollama": {"label": "Ollama (self-hosted)", "env_key": "", "needs_base": True,
               "models": ["llama3.1", "mistral", "qwen2.5", "gemma3"]},
    "custom": {"label": "Custom (full LiteLLM model string)", "env_key": "", "needs_base": False, "models": []},
}


def _fernet() -> Fernet:
    digest = hashlib.sha256(("llm-settings:" + get_settings().secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("Stored LLM API key cannot be decrypted (SECRET_KEY changed?)")
        return ""


def key_hint(key: str) -> str:
    if not key:
        return ""
    return f"{key[:7]}…{key[-4:]}" if len(key) > 14 else "…" + key[-2:]


class LLMConfig:
    """Effective configuration (env defaults merged with DB overrides)."""

    def __init__(self, data: dict | None = None):
        s = get_settings()
        d = data or {}
        self.source = "db" if d else "env"
        env_model = s.llm_model
        env_provider = env_model.split("/", 1)[0] if "/" in env_model else "custom"
        self.provider = d.get("provider") or env_provider
        self.model = d.get("model") or (env_model.split("/", 1)[1] if "/" in env_model else env_model)
        fast = d.get("fast_model") or (s.llm_fast_model.split("/", 1)[1] if "/" in s.llm_fast_model else s.llm_fast_model)
        self.fast_model = fast or self.model
        self.api_base = d.get("api_base") or ""
        self.api_key = decrypt(d["api_key_enc"]) if d.get("api_key_enc") else ""
        self.updated_at = d.get("updated_at")
        self.updated_by = d.get("updated_by", "")
        self.last_check = d.get("last_check")

    def model_string(self, fast: bool = False) -> str:
        name = self.fast_model if fast else self.model
        prefix = PROVIDERS.get(self.provider, {}).get("litellm_prefix")
        if prefix:  # aliased provider: model ids may themselves contain "/" (OpenRouter, vLLM) -> always prefix
            return f"{prefix}/{name}"
        if self.provider == "custom" or "/" in name:
            return name
        return f"{self.provider}/{name}"

    def litellm_kwargs(self) -> dict:
        import os

        kw: dict = {}
        if self.api_key:
            kw["api_key"] = self.api_key
        elif PROVIDERS.get(self.provider, {}).get("litellm_prefix"):
            # Aliased providers use a dedicated env var LiteLLM does not know about: pass it explicitly
            env_key = PROVIDERS[self.provider]["env_key"]
            if env_key and os.environ.get(env_key):
                kw["api_key"] = os.environ[env_key]
        if self.api_base:
            kw["api_base"] = self.api_base
        return kw

    def has_key(self) -> bool:
        import os

        if self.api_key:
            return True
        env_key = PROVIDERS.get(self.provider, {}).get("env_key")
        return bool(env_key and os.environ.get(env_key))

    def public(self) -> dict:
        import os

        env_key = PROVIDERS.get(self.provider, {}).get("env_key")
        hint = key_hint(self.api_key) if self.api_key else (key_hint(os.environ.get(env_key, "")) if env_key else "")
        return {
            "provider": self.provider, "model": self.model, "fast_model": self.fast_model, "api_base": self.api_base,
            "has_key": self.has_key(), "key_hint": hint, "key_source": "db" if self.api_key else ("env" if hint else None),
            "source": self.source, "updated_at": self.updated_at, "updated_by": self.updated_by,
            "last_check": self.last_check, "providers": {k: {"label": v["label"], "models": v["models"], "needs_base": v["needs_base"],
                              "free_model": not v["models"]} for k, v in PROVIDERS.items()},
        }


_cache: LLMConfig | None = None


def current() -> LLMConfig:
    return _cache if _cache is not None else LLMConfig()


async def load(session: AsyncSession) -> LLMConfig:
    global _cache
    row = await session.get(AppSetting, SETTING_KEY)
    _cache = LLMConfig(row.value if row else None)
    return _cache


async def save(session: AsyncSession, *, provider: str, model: str, fast_model: str, api_base: str,
               api_key: str | None, updated_by: str) -> LLMConfig:
    """api_key=None keeps the stored key; "" clears it; otherwise replaces it."""
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")
    row = await session.get(AppSetting, SETTING_KEY)
    data = dict(row.value) if row else {}
    data.update({"provider": provider, "model": model.strip(), "fast_model": fast_model.strip(),
                 "api_base": api_base.strip(), "updated_at": datetime.now(timezone.utc).isoformat(),
                 "updated_by": updated_by})
    if api_key is not None:
        if api_key:
            data["api_key_enc"] = encrypt(api_key.strip())
        else:
            data.pop("api_key_enc", None)
    if row:
        row.value = data
    else:
        session.add(AppSetting(key=SETTING_KEY, value=data))
    await session.flush()
    return await load(session)


async def record_check(session: AsyncSession, result: dict) -> None:
    row = await session.get(AppSetting, SETTING_KEY)
    if not row:
        return
    row.value = {**row.value, "last_check": result}
    await session.flush()
    await load(session)


async def test_connection(cfg: LLMConfig, fast: bool = False) -> dict:
    """One minimal completion; returns latency and a client-safe status (no provider error text)."""
    import litellm

    litellm.suppress_debug_info = True
    litellm.turn_off_message_logging = True
    model = cfg.model_string(fast)
    t0 = time.perf_counter()
    try:
        resp = await litellm.acompletion(model=model, messages=[{"role": "user", "content": "Reply with OK"}],
                                         max_tokens=5, temperature=0, timeout=30, num_retries=0, **cfg.litellm_kwargs())
        ms = int((time.perf_counter() - t0) * 1000)
        text = (resp.choices[0].message.content or "").strip()[:20]
        return {"ok": True, "latency_ms": ms, "model": model, "reply": text,
                "checked_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:  # classify without leaking provider payloads
        ms = int((time.perf_counter() - t0) * 1000)
        name = type(e).__name__
        reason = ("authentication" if "Auth" in name or "401" in str(e) else
                  "not_found" if "NotFound" in name or "404" in str(e) else
                  "rate_limited" if "RateLimit" in name or "429" in str(e) else
                  "timeout" if "Timeout" in name else "connection" if "Connection" in name or "APIConnection" in name
                  else "error")
        log.warning("LLM connection test failed for %s: %s: %s", model, name, str(e)[:200])
        return {"ok": False, "latency_ms": ms, "model": model, "reason": reason,
                "checked_at": datetime.now(timezone.utc).isoformat()}
