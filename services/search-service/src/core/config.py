from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["gemini", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Biblio Search Service"
    api_v1_prefix: str = "/api/v1"

    # Required
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY", min_length=1)
    database_url: str = Field(alias="DATABASE_URL", min_length=1)
    embedding_api_url: str = Field(alias="EMBEDDING_API_URL", min_length=1)

    # GCP / Vertex AI (aligned with Pipeline Worker naming)
    gcp_project_id: str = Field(default="", alias="GCP_PROJECT_ID")
    gcp_location: str = Field(default="us-central1", alias="GCP_LOCATION")

    # LLM provider
    llm_provider: LLMProvider = Field(default="gemini", alias="LLM_PROVIDER")
    gemini_model_name: str = Field(default="", alias="GEMINI_MODEL_NAME")
    llm_temperature: float = Field(
        default=0.2,
        alias="LLM_TEMPERATURE",
        ge=0.0,
        le=2.0,
    )
    llm_max_output_tokens: int = Field(
        default=512,
        alias="LLM_MAX_OUTPUT_TOKENS",
        ge=1,
    )

    # Search tuning
    search_top_k: int = Field(default=20, alias="SEARCH_TOP_K")
    final_top_k: int = Field(default=5, alias="FINAL_TOP_K")
    rrf_k: int = Field(default=60, alias="RRF_K")
    search_snapshot_ttl_hours: int = Field(
        default=168,
        alias="SEARCH_SNAPSHOT_TTL_HOURS",
        ge=1,
    )
    search_target_cache_ttl_sec: int = Field(
        default=60,
        alias="SEARCH_TARGET_CACHE_TTL_SEC",
        ge=1,
    )

    # Timeout & retry
    embedding_timeout_sec: int = Field(default=2, alias="EMBEDDING_TIMEOUT_SEC")
    embedding_max_retries: int = Field(default=1, alias="EMBEDDING_MAX_RETRIES")
    llm_timeout_sec: int = Field(default=3, alias="LLM_TIMEOUT_SEC")
    llm_max_retries: int = Field(default=1, alias="LLM_MAX_RETRIES")


@lru_cache
def get_settings() -> Settings:
    return Settings()
