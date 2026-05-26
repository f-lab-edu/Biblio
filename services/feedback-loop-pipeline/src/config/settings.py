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

    database_url: str = Field(alias="DATABASE_URL", min_length=1)
    broker_type: BrokerType = Field(default="inmemory", alias="BROKER_TYPE")

    raw_feedback_log_prefix: str = Field(alias="RAW_FEEDBACK_LOG_PREFIX", min_length=1)
    dataset_artifact_prefix: str = Field(alias="DATASET_ARTIFACT_PREFIX", min_length=1)
    model_artifact_prefix: str = Field(alias="MODEL_ARTIFACT_PREFIX", min_length=1)
    evaluation_artifact_prefix: str = Field(alias="EVALUATION_ARTIFACT_PREFIX", min_length=1)

    managed_embedding_endpoint_url: str = Field(alias="MANAGED_EMBEDDING_ENDPOINT_URL", min_length=1)
    local_training_model_name: str = Field(alias="LOCAL_TRAINING_MODEL_NAME", min_length=1)
    embedding_dimension: int = Field(alias="EMBEDDING_DIMENSION", ge=1)
    training_config_path: str = Field(alias="TRAINING_CONFIG_PATH", min_length=1)
    evaluation_dataset_ref: str = Field(alias="EVALUATION_DATASET_REF", min_length=1)

    worker_concurrency: int = Field(default=1, alias="WORKER_CONCURRENCY", ge=1)
    dataset_batch_size: int = Field(default=500, alias="DATASET_BATCH_SIZE", ge=1)
    min_deduped_event_count: int = Field(default=1, alias="MIN_DEDUPED_EVENT_COUNT", ge=1)
    min_training_group_count: int = Field(default=10, alias="MIN_TRAINING_GROUP_COUNT", ge=1)
    min_negative_count: int = Field(default=20, alias="MIN_NEGATIVE_COUNT", ge=1)
    training_timeout_sec: int = Field(default=900, alias="TRAINING_TIMEOUT_SEC", ge=1)
    evaluation_timeout_sec: int = Field(default=300, alias="EVALUATION_TIMEOUT_SEC", ge=1)
    rollback_restore_timeout_sec: int = Field(default=300, alias="ROLLBACK_RESTORE_TIMEOUT_SEC", ge=1)
    stuck_run_timeout_sec: int = Field(default=3600, alias="STUCK_RUN_TIMEOUT_SEC", ge=1)
    reconciliation_interval_sec: int = Field(default=60, alias="RECONCILIATION_INTERVAL_SEC", ge=1)
    max_retries: int = Field(default=3, alias="MAX_RETRIES", ge=0)
    retry_backoff_sec: float = Field(default=1.0, alias="RETRY_BACKOFF_SEC", ge=0.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
