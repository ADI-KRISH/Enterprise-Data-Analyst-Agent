"""Application settings loaded from environment / .env via pydantic-settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Agent code must only ever use the read-only URL.
    database_url_ro: SecretStr = Field(..., alias="DATABASE_URL_RO")
    # Admin URL is for database/seed.py only.
    database_url_admin: SecretStr | None = Field(None, alias="DATABASE_URL_ADMIN")

    # LLM provider: OpenRouter (OpenAI-compatible).
    openrouter_api_key: SecretStr | None = Field(None, alias="OPENROUTER_API_KEY")
    # OpenAI direct. With provider "auto", OPENAI_API_KEY (when set) is preferred over OpenRouter.
    openai_api_key: SecretStr | None = Field(None, alias="OPENAI_API_KEY")
    llm_provider: Literal["auto", "openai", "openrouter"] = Field("auto", alias="LLM_PROVIDER")
    openai_model_strong: str = Field("gpt-4o", alias="MODEL_NAME")
    openai_model_fast: str = Field("gpt-4o-mini", alias="OPENAI_MODEL_FAST")
    llm_model_strong: str = Field("nvidia/nemotron-3-ultra-550b-a55b:free", alias="LLM_MODEL_STRONG")
    llm_model_fast: str = Field("qwen/qwen3.8-27b:free", alias="LLM_MODEL_FAST")
    llm_model_fallback: str = Field(
        "nvidia/nemotron-3-super-120b-a12b:free", alias="LLM_MODEL_FALLBACK"
    )
    llm_timeout_s: float = Field(60.0, gt=0, alias="LLM_TIMEOUT_S")

    chroma_dir: Path = Field(PROJECT_ROOT / ".chroma", alias="CHROMA_DIR")
    embed_model: str = Field("BAAI/bge-small-en-v1.5", alias="EMBED_MODEL")
    embed_cache_dir: Path = Field(PROJECT_ROOT / ".cache" / "fastembed", alias="EMBED_CACHE_DIR")
    max_sql_attempts: int = Field(3, ge=1, le=10, alias="MAX_SQL_ATTEMPTS")
    default_row_limit: int = Field(1000, ge=1, le=100_000, alias="DEFAULT_ROW_LIMIT")
    # A LIMIT above this is clamped; the executor also never fetches more rows.
    max_result_rows: int = Field(5000, ge=1, le=100_000, alias="MAX_RESULT_ROWS")
    # EXPLAIN total-cost cap. Calibrated in Phase 3: the KPI reference queries in
    # retrieval/docs/kpi_definitions.md cost 2.2k-14.7k; cartesian joins cost 0.5M-122M.
    # Re-check against the eval gold queries (Phase 6).
    max_query_cost: float = Field(100_000.0, gt=0, alias="MAX_QUERY_COST")
    statement_timeout_ms: int = Field(15_000, ge=1, le=15_000, alias="STATEMENT_TIMEOUT_MS")
    # API (CLAUDE.md Security model §6)
    api_key: SecretStr | None = Field(None, alias="API_KEY")
    rate_limit_per_min: int = Field(30, ge=1, alias="RATE_LIMIT_PER_MIN")
    max_question_chars: int = Field(500, ge=1, alias="MAX_QUESTION_CHARS")
    request_timeout_s: float = Field(180.0, gt=0, alias="REQUEST_TIMEOUT_S")
    audit_log_path: Path = Field(PROJECT_ROOT / "logs" / "audit.jsonl", alias="AUDIT_LOG_PATH")


    @property
    def provider(self) -> str:
        if self.llm_provider != "auto":
            return self.llm_provider
        return "openai" if self.openai_api_key is not None else "openrouter"


@lru_cache
def get_settings() -> Settings:
    return Settings()
