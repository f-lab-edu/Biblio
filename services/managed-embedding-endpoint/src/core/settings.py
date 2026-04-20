from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Biblio Managed Embedding Endpoint"

    # Server
    port: int = Field(default=8000, alias="PORT")

    # Model (required)
    model_artifact_path: str = Field(alias="MODEL_ARTIFACT_PATH", min_length=1)

    # Model cache (optional)
    model_cache_dir: str = Field(default="", alias="MODEL_CACHE_DIR")

    # Guardrails
    max_texts_per_request: int = Field(default=32, alias="MAX_TEXTS_PER_REQUEST", ge=1)
    max_text_length_chars: int = Field(default=4096, alias="MAX_TEXT_LENGTH_CHARS", ge=1)
    max_payload_bytes: int = Field(default=262144, alias="MAX_PAYLOAD_BYTES", ge=1)
    max_concurrency: int = Field(default=1, alias="MAX_CONCURRENCY", ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
