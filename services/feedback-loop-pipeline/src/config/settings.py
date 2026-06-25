from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BrokerType = Literal["pgmq", "inmemory"]
ArtifactStoreBackend = Literal["local", "gcs"]
AppRole = Literal[
    "scheduler",
    "dataset-worker",
    "train-release-worker",
    "rollback-worker",
    "legacy-reindex-worker",
    "reembedding-worker",
]
Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = Field(alias="DATABASE_URL", min_length=1)
    broker_type: BrokerType = Field(default="inmemory", alias="BROKER_TYPE")
    app_role: AppRole = Field(default="scheduler", alias="APP_ROLE")

    raw_feedback_log_prefix: str = Field(alias="RAW_FEEDBACK_LOG_PREFIX", min_length=1)
    dataset_artifact_prefix: str = Field(alias="DATASET_ARTIFACT_PREFIX", min_length=1)
    model_artifact_prefix: str = Field(alias="MODEL_ARTIFACT_PREFIX", min_length=1)
    model_version_prefix: str = Field(alias="MODEL_VERSION_PREFIX", min_length=1)
    serving_model_artifact_prefix: str = Field(alias="SERVING_MODEL_ARTIFACT_PREFIX", min_length=1)
    evaluation_artifact_prefix: str = Field(alias="EVALUATION_ARTIFACT_PREFIX", min_length=1)
    artifact_store_backend: ArtifactStoreBackend = Field(default="local", alias="ARTIFACT_STORE_BACKEND")
    gcs_feedback_log_bucket_name: str | None = Field(default=None, alias="GCS_FEEDBACK_LOG_BUCKET_NAME")
    gcs_ml_artifact_bucket_name: str | None = Field(default=None, alias="GCS_ML_ARTIFACT_BUCKET_NAME")
    local_artifact_root: str = Field(default="./tmp/feedback-loop-artifacts", alias="LOCAL_ARTIFACT_ROOT", min_length=1)

    managed_embedding_endpoint_url: str = Field(alias="MANAGED_EMBEDDING_ENDPOINT_URL", min_length=1)
    search_service_url: str = Field(default="", alias="SEARCH_SERVICE_URL")
    local_training_model_name: str = Field(alias="LOCAL_TRAINING_MODEL_NAME", min_length=1)
    embedding_dimension: int = Field(alias="EMBEDDING_DIMENSION", ge=1)
    training_config_path: str = Field(alias="TRAINING_CONFIG_PATH", min_length=1)
    evaluation_dataset_ref: str = Field(alias="EVALUATION_DATASET_REF", min_length=1)

    worker_concurrency: int = Field(default=1, alias="WORKER_CONCURRENCY", ge=1)
    worker_poll_interval_sec: float = Field(default=1.0, alias="WORKER_POLL_INTERVAL_SEC", ge=0.1)
    scheduler_tick_interval_sec: int = Field(default=60, alias="SCHEDULER_TICK_INTERVAL_SEC", ge=1)
    dataset_generation_hour_kst: int = Field(default=3, alias="DATASET_GENERATION_HOUR_KST", ge=0, le=23)
    dataset_generation_minute_kst: int = Field(default=0, alias="DATASET_GENERATION_MINUTE_KST", ge=0, le=59)
    training_request_weekday_kst: Weekday = Field(default="mon", alias="TRAINING_REQUEST_WEEKDAY_KST")
    training_request_hour_kst: int = Field(default=4, alias="TRAINING_REQUEST_HOUR_KST", ge=0, le=23)
    training_request_minute_kst: int = Field(default=0, alias="TRAINING_REQUEST_MINUTE_KST", ge=0, le=59)
    feedback_dataset_queue_name: str = Field(default="feedback.dataset", alias="FEEDBACK_DATASET_QUEUE_NAME", min_length=1)
    feedback_training_queue_name: str = Field(default="feedback.training", alias="FEEDBACK_TRAINING_QUEUE_NAME", min_length=1)
    feedback_rollback_queue_name: str = Field(default="feedback.rollback.high", alias="FEEDBACK_ROLLBACK_QUEUE_NAME", min_length=1)
    feedback_reembedding_queue_name: str = Field(default="feedback.reembedding", alias="FEEDBACK_REEMBEDDING_QUEUE_NAME", min_length=1)
    dataset_batch_size: int = Field(default=500, alias="DATASET_BATCH_SIZE", ge=1)
    min_deduped_event_count: int = Field(default=1, alias="MIN_DEDUPED_EVENT_COUNT", ge=1)
    min_training_group_count: int = Field(default=10, alias="MIN_TRAINING_GROUP_COUNT", ge=1)
    min_negative_count: int = Field(default=20, alias="MIN_NEGATIVE_COUNT", ge=1)
    training_timeout_sec: int = Field(default=900, alias="TRAINING_TIMEOUT_SEC", ge=1)
    evaluation_timeout_sec: int = Field(default=300, alias="EVALUATION_TIMEOUT_SEC", ge=1)
    rollback_restore_timeout_sec: int = Field(default=300, alias="ROLLBACK_RESTORE_TIMEOUT_SEC", ge=1)
    stuck_run_timeout_sec: int = Field(default=3600, alias="STUCK_RUN_TIMEOUT_SEC", ge=1)
    reconciliation_interval_sec: int = Field(default=60, alias="RECONCILIATION_INTERVAL_SEC", ge=1)
    legacy_reindex_scan_interval_sec: int = Field(default=60, alias="LEGACY_REINDEX_SCAN_INTERVAL_SEC", ge=1)
    legacy_reindex_batch_size: int = Field(default=8, alias="LEGACY_REINDEX_BATCH_SIZE", ge=1)
    legacy_reindex_per_run_video_limit: int = Field(default=100, alias="LEGACY_REINDEX_PER_RUN_VIDEO_LIMIT", ge=1)
    legacy_reindex_throttle_sleep_ms: int = Field(default=0, alias="LEGACY_REINDEX_THROTTLE_SLEEP_MS", ge=0)
    candidate_deployment_max_attempts: int = Field(default=5, alias="CANDIDATE_DEPLOYMENT_MAX_ATTEMPTS", ge=1)
    rollback_reembed_batch_size: int = Field(default=8, alias="ROLLBACK_REEMBED_BATCH_SIZE", ge=1)
    max_retries: int = Field(default=3, alias="MAX_RETRIES", ge=0)
    retry_backoff_sec: float = Field(default=1.0, alias="RETRY_BACKOFF_SEC", ge=0.0)

    @field_validator("gcs_feedback_log_bucket_name", "gcs_ml_artifact_bucket_name", mode="before")
    @classmethod
    def _blank_gcs_bucket_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def _require_gcs_buckets_for_gcs_backend(self) -> "Settings":
        if self.artifact_store_backend == "gcs" and not self.gcs_feedback_log_bucket_name:
            raise ValueError("GCS_FEEDBACK_LOG_BUCKET_NAME is required when ARTIFACT_STORE_BACKEND=gcs")
        if self.artifact_store_backend == "gcs" and not self.gcs_ml_artifact_bucket_name:
            raise ValueError("GCS_ML_ARTIFACT_BUCKET_NAME is required when ARTIFACT_STORE_BACKEND=gcs")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
