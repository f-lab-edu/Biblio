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
    stt_location: str = Field(default="us", alias="STT_LOCATION")
    vision_location: str = Field(default="global", alias="VISION_LOCATION")
    gcs_video_bucket_name: str = Field(alias="GCS_VIDEO_BUCKET_NAME")
    embedding_api_url: str = Field(alias="EMBEDDING_API_URL")

    worker_concurrency: int = Field(default=4, alias="WORKER_CONCURRENCY", ge=1)
    queue_visibility_timeout_sec: int = Field(
        default=1800,
        alias="QUEUE_VISIBILITY_TIMEOUT_SEC",
        ge=1,
    )
    delete_queue_visibility_timeout_sec: int = Field(
        default=300,
        alias="DELETE_QUEUE_VISIBILITY_TIMEOUT_SEC",
        ge=1,
    )
    stale_processing_reclaim_sec: int = Field(
        default=1500,
        alias="STALE_PROCESSING_RECLAIM_SEC",
        ge=1,
    )
    max_retries: int = Field(default=3, alias="MAX_RETRIES", ge=0)
    download_timeout_sec: int = Field(default=600, alias="DOWNLOAD_TIMEOUT_SEC", ge=1)
    youtube_max_duration_sec: int = Field(default=1800, alias="YOUTUBE_MAX_DURATION_SEC", ge=1)
    youtube_max_filesize_bytes: int = Field(
        default=500 * 1024 * 1024,
        alias="YOUTUBE_MAX_FILESIZE_BYTES",
        ge=1,
    )
    youtube_max_height: int = Field(default=720, alias="YOUTUBE_MAX_HEIGHT", ge=1)
    youtube_proxy_url: str = Field(default="", alias="YOUTUBE_PROXY_URL")
    stt_submit_timeout_sec: int = Field(default=30, alias="STT_SUBMIT_TIMEOUT_SEC", ge=1)
    stt_operation_timeout_sec: int = Field(default=900, alias="STT_OPERATION_TIMEOUT_SEC", ge=1)
    stt_recognizer: str = Field(default="", alias="STT_RECOGNIZER")
    stt_model_version: str = Field(default="", alias="STT_MODEL_VERSION")
    embedding_model_version: str = Field(default="", alias="EMBEDDING_MODEL_VERSION")
    vision_model: str = Field(default="gemini-3.1-flash-lite", alias="VISION_MODEL")
    vision_timeout_sec: int = Field(default=15, alias="VISION_TIMEOUT_SEC", ge=1)
    vision_max_output_tokens: int = Field(
        default=512,
        alias="VISION_MAX_OUTPUT_TOKENS",
        ge=1,
    )
    embedding_timeout_sec: int = Field(default=10, alias="EMBEDDING_TIMEOUT_SEC", ge=1)
    embedding_batch_size: int = Field(default=16, alias="EMBEDDING_BATCH_SIZE", ge=1)
    chunk_max_tokens: int = Field(default=300, alias="CHUNK_MAX_TOKENS", ge=1)
    chunk_overlap_sentences: int = Field(default=1, alias="CHUNK_OVERLAP_SENTENCES", ge=0)
    poll_interval_sec: float = Field(default=1.0, alias="POLL_INTERVAL_SEC", ge=0.1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
