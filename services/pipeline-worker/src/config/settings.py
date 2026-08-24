from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
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

    queue_visibility_timeout_sec: int = Field(
        default=1800,
        alias="QUEUE_VISIBILITY_TIMEOUT_SEC",
        ge=1,
    )
    normalization_queue_visibility_timeout_sec: int = Field(
        default=7200,
        alias="NORMALIZATION_QUEUE_VISIBILITY_TIMEOUT_SEC",
        ge=1,
    )
    normalization_signed_url_ttl_sec: int = Field(
        default=8100,
        alias="NORMALIZATION_SIGNED_URL_TTL_SEC",
        ge=1,
    )
    transcription_queue_visibility_timeout_sec: int = Field(
        default=4200,
        alias="TRANSCRIPTION_QUEUE_VISIBILITY_TIMEOUT_SEC",
        ge=1,
    )
    enrichment_queue_visibility_timeout_sec: int = Field(
        default=120,
        alias="ENRICHMENT_QUEUE_VISIBILITY_TIMEOUT_SEC",
        ge=1,
    )
    embedding_queue_visibility_timeout_sec: int = Field(
        default=300,
        alias="EMBEDDING_QUEUE_VISIBILITY_TIMEOUT_SEC",
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
    stage_max_delivery_attempts: int = Field(
        default=3,
        alias="STAGE_MAX_DELIVERY_ATTEMPTS",
        ge=1,
    )
    download_timeout_sec: int = Field(default=600, alias="DOWNLOAD_TIMEOUT_SEC", ge=1)
    max_audio_duration_sec: int = Field(default=3600, alias="MAX_AUDIO_DURATION_SEC", ge=1)
    audio_part_duration_sec: int = Field(default=900, alias="AUDIO_PART_DURATION_SEC", ge=1)
    audio_part_overlap_sec: int = Field(default=5, alias="AUDIO_PART_OVERLAP_SEC", ge=0)
    stt_part_concurrency: int = Field(default=8, alias="STT_PART_CONCURRENCY", ge=1)
    normalization_concurrency: int = Field(
        default=1,
        alias="NORMALIZATION_CONCURRENCY",
        ge=1,
        le=2,
    )
    enrichment_concurrency: int = Field(
        default=4,
        alias="ENRICHMENT_CONCURRENCY",
        ge=1,
    )
    embedding_concurrency: int = Field(
        default=1,
        alias="EMBEDDING_CONCURRENCY",
        ge=1,
    )
    frame_candidate_interval_sec: int = Field(
        default=60,
        alias="FRAME_CANDIDATE_INTERVAL_SEC",
        ge=1,
    )
    frame_candidate_max_width: int = Field(
        default=1280,
        alias="FRAME_CANDIDATE_MAX_WIDTH",
        ge=1,
    )
    audio_processing_timeout_sec: int = Field(
        default=120,
        alias="AUDIO_PROCESSING_TIMEOUT_SEC",
        ge=1,
    )
    youtube_max_duration_sec: int = Field(default=3600, alias="YOUTUBE_MAX_DURATION_SEC", ge=1)
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
    embedding_timeout_sec: int = Field(default=30, alias="EMBEDDING_TIMEOUT_SEC", ge=1)
    embedding_batch_size: int = Field(default=16, alias="EMBEDDING_BATCH_SIZE", ge=1)
    embedding_batch_max_wait_ms: int = Field(
        default=0,
        alias="EMBEDDING_BATCH_MAX_WAIT_MS",
        ge=0,
    )
    chunk_max_tokens: int = Field(default=300, alias="CHUNK_MAX_TOKENS", ge=1)
    chunk_overlap_sentences: int = Field(default=1, alias="CHUNK_OVERLAP_SENTENCES", ge=0)
    poll_interval_sec: float = Field(default=1.0, alias="POLL_INTERVAL_SEC", ge=0.1)
    queue_sample_interval_sec: float = Field(
        default=0.0,
        alias="QUEUE_SAMPLE_INTERVAL_SEC",
        ge=0.0,
    )
    worker_process_sample_interval_sec: float = Field(
        default=0.0,
        alias="WORKER_PROCESS_SAMPLE_INTERVAL_SEC",
        ge=0.0,
    )
    pipeline_version: str = Field(
        default="work-unit-v1",
        alias="PIPELINE_VERSION",
        min_length=1,
    )
    recovery_scan_interval_sec: float = Field(
        default=30.0,
        alias="RECOVERY_SCAN_INTERVAL_SEC",
        gt=0.0,
    )

    @model_validator(mode="after")
    def validate_audio_part_settings(self) -> Self:
        if self.audio_part_overlap_sec >= self.audio_part_duration_sec:
            raise ValueError("AUDIO_PART_OVERLAP_SEC must be less than AUDIO_PART_DURATION_SEC")
        if self.audio_part_duration_sec + self.audio_part_overlap_sec > 20 * 60:
            raise ValueError("Audio parts including overlap must not exceed 20 minutes")
        return self

    @model_validator(mode="after")
    def validate_stage_visibility_timeouts(self) -> Self:
        minimums = (
            (
                self.normalization_queue_visibility_timeout_sec,
                self.max_audio_duration_sec,
                "NORMALIZATION_QUEUE_VISIBILITY_TIMEOUT_SEC",
            ),
            (
                self.transcription_queue_visibility_timeout_sec,
                self.stt_submit_timeout_sec + self.stt_operation_timeout_sec,
                "TRANSCRIPTION_QUEUE_VISIBILITY_TIMEOUT_SEC",
            ),
            (
                self.enrichment_queue_visibility_timeout_sec,
                self.vision_timeout_sec,
                "ENRICHMENT_QUEUE_VISIBILITY_TIMEOUT_SEC",
            ),
            (
                self.embedding_queue_visibility_timeout_sec,
                self.embedding_timeout_sec,
                "EMBEDDING_QUEUE_VISIBILITY_TIMEOUT_SEC",
            ),
        )
        for visibility_timeout, operation_timeout, setting_name in minimums:
            if visibility_timeout <= operation_timeout:
                raise ValueError(
                    f"{setting_name} must be greater than its stage operation timeout"
                )
        if (
            self.normalization_signed_url_ttl_sec
            <= self.normalization_queue_visibility_timeout_sec
        ):
            raise ValueError(
                "NORMALIZATION_SIGNED_URL_TTL_SEC must be greater than "
                "NORMALIZATION_QUEUE_VISIBILITY_TIMEOUT_SEC"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
