

def test_openai_compatible_maps_to_openai_prefix(monkeypatch):
    from app.services.llm_settings import LLMConfig

    cfg = LLMConfig({"provider": "openai_compatible", "model": "meta-llama/Llama-3.1-70B-Instruct",
                     "api_base": "http://vllm:8000/v1"})
    assert cfg.model_string() == "openai/meta-llama/Llama-3.1-70B-Instruct"
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-local-test")
    kw = cfg.litellm_kwargs()
    assert kw["api_base"] == "http://vllm:8000/v1" and kw["api_key"] == "sk-local-test"
    assert cfg.has_key() and cfg.public()["providers"]["openai_compatible"]["free_model"] is True


async def test_list_models_openai_compatible(monkeypatch):
    import httpx

    from app.services import llm_settings
    from app.services.llm_settings import LLMConfig

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers.get("authorization")
        return httpx.Response(200, json={"data": [{"id": "qwen2.5-7b"}, {"id": "meta-llama/Llama-3.1-8B"}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    cfg = LLMConfig({"provider": "openai_compatible", "model": "x", "api_base": "http://vllm:8000",
                     "api_key_enc": llm_settings.encrypt("sk-test")})
    res = await llm_settings.list_models(cfg)
    assert res == {"ok": True, "models": ["meta-llama/Llama-3.1-8B", "qwen2.5-7b"], "reason": None}
    assert seen["url"] == "http://vllm:8000/v1/models" and seen["auth"] == "Bearer sk-test"

    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(401)), **kw))
    assert (await llm_settings.list_models(cfg))["reason"] == "authentication"
    assert (await llm_settings.list_models(LLMConfig({"provider": "azure", "model": "d", "api_base": "https://a"})))["reason"] == "unsupported"
