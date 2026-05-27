from functools import lru_cache
from pathlib import Path

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
    model_artifact_root: str = Field(default="", alias="MODEL_ARTIFACT_ROOT")

    # Model cache (optional)
    model_cache_dir: str = Field(default="", alias="MODEL_CACHE_DIR")

    # ModelRelease read model (optional for local single-model bootstrap)
    database_url: str = Field(default="", alias="DATABASE_URL")

    # Guardrails
    max_texts_per_request: int = Field(default=32, alias="MAX_TEXTS_PER_REQUEST", ge=1)
    max_text_length_chars: int = Field(default=4096, alias="MAX_TEXT_LENGTH_CHARS", ge=1)
    max_payload_bytes: int = Field(default=262144, alias="MAX_PAYLOAD_BYTES", ge=1)
    max_concurrency: int = Field(default=1, alias="MAX_CONCURRENCY", ge=1)

    @property
    def bootstrap_model_version(self) -> str:
        return Path(self.model_artifact_path).name


@lru_cache
def get_settings() -> Settings:
    return Settings()
