"""Application configuration.

Every setting comes from an environment variable (portability rule 1). This module is the
only place that reads the environment; everything else receives a ``Settings`` instance.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]
AuthMode = Literal["dev", "jwks"]
LLMProviderName = Literal["groq", "openai_compatible"]
ReasoningEffort = Literal["none", "low", "medium", "high"]


class Settings(BaseSettings):
    """All runtime configuration. Field names map to UPPER_SNAKE_CASE env vars."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,  # `KEY=` in .env means "unset", not ""
    )

    app_name: str = "MedCite"
    app_env: AppEnv = "development"
    log_level: str = "INFO"
    read_only: bool = False
    cors_origins: str = "http://localhost:5173"  # comma-separated

    database_url: str = "postgresql+asyncpg://postgres:dev@localhost:5432/medcite"
    database_url_direct: str | None = None  # Alembic; falls back to database_url
    db_statement_cache_size: int = Field(default=100, ge=0)
    db_pool_size: int = Field(default=5, ge=1)
    db_connect_timeout_s: float = Field(default=5.0, gt=0)

    redis_url: str = "redis://localhost:6379/0"
    redis_connect_timeout_s: float = Field(default=2.0, gt=0)

    ncbi_api_key: SecretStr | None = None
    ncbi_tool: str = "medcite"
    ncbi_email: str = "you@example.com"
    ncbi_max_rps: int = Field(default=9, ge=1, le=10)
    ncbi_base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    ncbi_timeout_s: float = Field(default=15.0, gt=0)
    ncbi_retry_attempts: int = Field(default=4, ge=1)
    ncbi_cache_ttl_s: int = Field(default=86_400, ge=0)

    llm_provider: LLMProviderName = "groq"
    llm_api_key: SecretStr | None = None
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model_planner: str = "openai/gpt-oss-20b"
    llm_model_generator: str = "openai/gpt-oss-120b"
    llm_model_judge: str = "qwen/qwen3.8-27b"
    # Reasoning tokens count toward Groq's tokens-per-minute limit.
    llm_reasoning_planner: ReasoningEffort = "low"
    llm_reasoning_generator: ReasoningEffort = "medium"
    llm_reasoning_judge: ReasoningEffort = "low"
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0)
    # USD per million tokens [input, output] by model, as JSON; unlisted models cost 0.
    llm_prices: dict[str, tuple[float, float]] = Field(default_factory=dict)

    embed_query_model: str = "ncbi/MedCPT-Query-Encoder"
    embed_article_model: str = "ncbi/MedCPT-Article-Encoder"
    rerank_model: str = "ncbi/MedCPT-Cross-Encoder"
    hf_home: str = "./.models"

    auth_mode: AuthMode = "dev"
    auth_jwks_url: str | None = None
    auth_issuer: str | None = None
    auth_audience: str = "authenticated"
    auth_dev_signing_key: SecretStr | None = None

    user_daily_cost_cap_usd: Decimal = Decimal("2.00")
    discovery_max_articles: int = Field(default=200, ge=1)

    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str | None = None
    sentry_dsn: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def migrations_database_url(self) -> str:
        return self.database_url_direct or self.database_url

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if self.auth_mode == "dev" and self.app_env != "development":
            raise ValueError("AUTH_MODE=dev is only allowed when APP_ENV=development")
        if self.auth_mode == "jwks" and not (self.auth_jwks_url and self.auth_issuer):
            raise ValueError("AUTH_MODE=jwks requires AUTH_JWKS_URL and AUTH_ISSUER")
        if not self.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL must use the postgresql+asyncpg:// driver prefix")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read once (rule 1)."""
    return Settings()
