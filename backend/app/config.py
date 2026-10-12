from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://usecasehub:usecasehub@localhost:5432/usecasehub"
    secret_key: str = "dev-secret-change-me"
    access_token_expire_minutes: int = 720
    admin_email: str = "admin@example.com"
    admin_password: str = "admin1234"

    llm_model: str = "anthropic/claude-sonnet-4-5"
    llm_fast_model: str = ""
    llm_temperature: float = 0.2
    embedding_model: str = "hash"
    embedding_dims: int = 384

    overlap_candidate_threshold: float = 0.55   # semantic embedding models
    overlap_fallback_threshold: float = 0.72    # similarity-only overlap when no LLM judge
    hash_candidate_threshold: float = 0.30      # key-less hash embeddings score lower
    hash_fallback_threshold: float = 0.37
    overlap_max_candidates: int = 8
    auto_analyze_on_change: bool = True

    @property
    def candidate_threshold(self) -> float:
        return self.hash_candidate_threshold if self.embedding_model == "hash" else self.overlap_candidate_threshold

    @property
    def fallback_threshold(self) -> float:
        return self.hash_fallback_threshold if self.embedding_model == "hash" else self.overlap_fallback_threshold

    seed_demo_data: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    templates_dir: Path = Path(__file__).parent / "templates"
    uploads_dir: Path = Path("uploads")

    @property
    def fast_model(self) -> str:
        return self.llm_fast_model or self.llm_model

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
