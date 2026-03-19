from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BrokerType = Literal["pgmq", "inmemory"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    broker_type: BrokerType = Field(alias="BROKER_TYPE")
    database_url: str = Field(alias="DATABASE_URL")
    gcp_project_id: str = Field(alias="GCP_PROJECT_ID")
    gcs_video_bucket_name: str = Field(alias="GCS_VIDEO_BUCKET_NAME")
    embedding_api_url: str = Field(alias="EMBEDDING_API_URL")

    worker_concurrency: int = Field(default=4, alias="WORKER_CONCURRENCY", ge=1)
    max_retries: int = Field(default=3, alias="MAX_RETRIES", ge=0)
    download_timeout_sec: int = Field(default=60, alias="DOWNLOAD_TIMEOUT_SEC", ge=1)
    stt_timeout_sec: int = Field(default=120, alias="STT_TIMEOUT_SEC", ge=1)
    stt_model_version: str = Field(default="", alias="STT_MODEL_VERSION")
    embedding_model_version: str = Field(default="", alias="EMBEDDING_MODEL_VERSION")
    vision_timeout_sec: int = Field(default=15, alias="VISION_TIMEOUT_SEC", ge=1)
    embedding_timeout_sec: int = Field(default=10, alias="EMBEDDING_TIMEOUT_SEC", ge=1)
    embedding_batch_size: int = Field(default=16, alias="EMBEDDING_BATCH_SIZE", ge=1)
    chunk_max_tokens: int = Field(default=300, alias="CHUNK_MAX_TOKENS", ge=1)
    chunk_overlap_sentences: int = Field(default=1, alias="CHUNK_OVERLAP_SENTENCES", ge=0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
