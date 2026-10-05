"""Application settings loaded from environment / .env via pydantic-settings."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Agent code must only ever use the read-only URL.
    database_url_ro: str = Field(..., alias="DATABASE_URL_RO")
    # Admin URL is for database/seed.py only.
    database_url_admin: str | None = Field(None, alias="DATABASE_URL_ADMIN")

    # LLM provider: OpenRouter (OpenAI-compatible).
    openrouter_api_key: SecretStr | None = Field(None, alias="OPENROUTER_API_KEY")
    llm_model_strong: str = Field("nvidia/nemotron-3-ultra-550b-a55b:free", alias="LLM_MODEL_STRONG")
    llm_model_fast: str = Field("qwen/qwen3.8-27b:free", alias="LLM_MODEL_FAST")
    llm_model_fallback: str = Field(
        "nvidia/nemotron-3-super-120b-a12b:free", alias="LLM_MODEL_FALLBACK"
    )
    llm_timeout_s: float = Field(60.0, gt=0, alias="LLM_TIMEOUT_S")

    @field_validator("openrouter_api_key", "database_url_admin", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    chroma_dir: Path = Field(PROJECT_ROOT / ".chroma", alias="CHROMA_DIR")
    max_sql_attempts: int = Field(3, ge=1, le=10, alias="MAX_SQL_ATTEMPTS")
    default_row_limit: int = Field(1000, ge=1, le=100_000, alias="DEFAULT_ROW_LIMIT")


@lru_cache
def get_settings() -> Settings:
    return Settings()
