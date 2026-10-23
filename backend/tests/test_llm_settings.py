

def test_openai_compatible_maps_to_openai_prefix(monkeypatch):
    from app.services.llm_settings import LLMConfig

    cfg = LLMConfig({"provider": "openai_compatible", "model": "meta-llama/Llama-3.1-70B-Instruct",
                     "api_base": "http://vllm:8000/v1"})
    assert cfg.model_string() == "openai/meta-llama/Llama-3.1-70B-Instruct"
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "sk-local-test")
    kw = cfg.litellm_kwargs()
    assert kw["api_base"] == "http://vllm:8000/v1" and kw["api_key"] == "sk-local-test"
    assert cfg.has_key() and cfg.public()["providers"]["openai_compatible"]["free_model"] is True
