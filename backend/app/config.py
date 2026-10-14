from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRETS = {"", "dev-secret-change-me", "change-me-to-a-long-random-string", "changeme", "secret"}
INSECURE_PASSWORDS = {"", "admin1234", "admin", "password", "changeme", "usecasehub"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # "production" (default) refuses to start with insecure secrets; "dev" relaxes the checks.
    app_env: str = "production"

    database_url: str = "postgresql+asyncpg://usecasehub:usecasehub@localhost:5432/usecasehub"
    secret_key: str = ""
    access_token_expire_minutes: int = 60
    admin_email: str = "admin@example.com"
    admin_password: str = ""
    password_min_length: int = 12
    login_max_failures: int = 5          # per account and per client IP, within login_window_seconds
    login_window_seconds: int = 900
    login_lockout_seconds: int = 900

    llm_model: str = "anthropic/claude-sonnet-4-5"
    llm_fast_model: str = ""
    llm_temperature: float = 0.2
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 1
    # Optional allowlist of LiteLLM provider prefixes (e.g. "azure,bedrock") to enforce a data-residency choice.
    llm_allowed_providers: str = ""
    ai_rate_limit_per_minute: int = 20   # AI-triggering calls per user per minute
    embedding_model: str = "hash"
    embedding_dims: int = 384

    overlap_candidate_threshold: float = 0.55   # semantic embedding models
    overlap_fallback_threshold: float = 0.72    # similarity-only overlap when no LLM judge
    hash_candidate_threshold: float = 0.30      # key-less hash embeddings score lower
    hash_fallback_threshold: float = 0.37
    overlap_max_candidates: int = 8
    auto_analyze_on_change: bool = True
    job_lease_seconds: int = 1800        # a "running" job older than this is considered orphaned

    seed_demo_data: bool = False
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    templates_dir: Path = Path(__file__).parent / "templates"
    uploads_dir: Path = Path("uploads")
    upload_max_bytes: int = 10 * 1024 * 1024

    @property
    def is_dev(self) -> bool:
        return self.app_env.lower() in ("dev", "development", "test")

    @model_validator(mode="after")
    def _fail_fast_on_insecure_config(self):
        if self.is_dev:
            if not self.secret_key:
                self.secret_key = "dev-only-secret-not-for-production-use-0000"
            if not self.admin_password:
                self.admin_password = "admin1234"
            return self
        problems = []
        if self.secret_key in INSECURE_SECRETS or len(self.secret_key) < 32:
            problems.append("SECRET_KEY must be a random string of at least 32 characters")
        if self.admin_password in INSECURE_PASSWORDS or len(self.admin_password) < self.password_min_length:
            problems.append(f"ADMIN_PASSWORD must be at least {self.password_min_length} characters and not a default")
        if ":usecasehub@" in self.database_url or ":postgres@" in self.database_url:
            problems.append("DATABASE_URL uses a default database password")
        if self.seed_demo_data:
            problems.append("SEED_DEMO_DATA must be false outside APP_ENV=dev")
        if problems:
            raise ValueError("Refusing to start with insecure configuration (set APP_ENV=dev for local work):\n  - "
                             + "\n  - ".join(problems))
        return self

    @property
    def candidate_threshold(self) -> float:
        return self.hash_candidate_threshold if self.embedding_model == "hash" else self.overlap_candidate_threshold

    @property
    def fallback_threshold(self) -> float:
        return self.hash_fallback_threshold if self.embedding_model == "hash" else self.overlap_fallback_threshold

    @property
    def fast_model(self) -> str:
        return self.llm_fast_model or self.llm_model

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def allowed_providers(self) -> list[str]:
        return [p.strip().lower() for p in self.llm_allowed_providers.split(",") if p.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
