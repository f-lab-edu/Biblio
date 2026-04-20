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


@lru_cache
def get_settings() -> Settings:
    return Settings()
