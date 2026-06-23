from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BrokerType = Literal["pgmq", "inmemory"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Biblio Core API"
    api_v1_prefix: str = "/api/v1"
    gcp_project_id: str = Field(alias="GCP_PROJECT_ID", min_length=1)
    gcs_video_bucket_name: str = Field(alias="GCS_VIDEO_BUCKET_NAME", min_length=1)
    jwt_secret_key: str = Field(alias="JWT_SECRET_KEY", min_length=1)
    database_url: str = Field(alias="DATABASE_URL", min_length=1)
    broker_type: BrokerType = Field(default="pgmq", alias="BROKER_TYPE")
    fip_feedback_delivery_url: str = Field(
        default="https://feedback-ingestion-pipeline:8080/feedback/events",
        alias="FIP_FEEDBACK_DELIVERY_URL",
        min_length=1,
    )
    fip_delivery_use_iam_auth: bool = Field(
        default=False,
        alias="FIP_DELIVERY_USE_IAM_AUTH",
    )
    feedback_delivery_timeout_seconds: float = Field(
        default=2.0,
        alias="FEEDBACK_DELIVERY_TIMEOUT_SECONDS",
        gt=0,
    )
    feedback_delivery_max_attempts: int = Field(
        default=3,
        alias="FEEDBACK_DELIVERY_MAX_ATTEMPTS",
        ge=1,
    )
    feedback_delivery_retry_delay_seconds: float = Field(
        default=0.0,
        alias="FEEDBACK_DELIVERY_RETRY_DELAY_SECONDS",
        ge=0,
    )
    feedback_rollback_queue_name: str = Field(
        default="feedback.rollback.high",
        alias="FEEDBACK_ROLLBACK_QUEUE_NAME",
        min_length=1,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
