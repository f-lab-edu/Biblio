from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

Category = Literal["GENERAL", "IT", "MEDICAL", "LEGAL"]
InputType = Literal["LOCAL_FILE", "EXTERNAL_URL"]
VideoStatus = Literal["PENDING", "UPLOADED", "PROCESSING", "READY", "FAILED", "DELETING"]
FailedStage = Literal[
    "DOWNLOAD",
    "EXTRACT",
    "STT",
    "CHUNKING",
    "EMBEDDING",
    "VECTOR_UPSERT",
    "NORMALIZE_VIDEO",
    "TRANSCRIBE_PART",
    "ASSEMBLE_CHUNKS",
    "ENRICH_CHUNK",
    "EMBED_BATCH",
]
SUPPORTED_FILE_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi", ".wmv"})
UNSUPPORTED_FILE_TYPE_ERROR = "unsupported_file_type"


class VideoCreateBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: Category


class LocalFileVideoCreateRequest(VideoCreateBase):
    input_type: Literal["LOCAL_FILE"]
    extension: str

    @field_validator("extension", mode="before")
    @classmethod
    def normalize_and_validate_extension(cls, value: object) -> str:
        normalized = value.lower() if isinstance(value, str) else ""
        if normalized not in SUPPORTED_FILE_EXTENSIONS:
            raise PydanticCustomError(
                UNSUPPORTED_FILE_TYPE_ERROR,
                "Unsupported video file extension.",
            )
        return normalized


class ExternalUrlVideoCreateRequest(VideoCreateBase):
    input_type: Literal["EXTERNAL_URL"]
    source_url: AnyHttpUrl

    @field_validator("source_url")
    @classmethod
    def validate_youtube_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        host = value.host or ""
        allowed_hosts = {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "music.youtube.com",
            "youtu.be",
        }
        if host.lower() not in allowed_hosts:
            raise ValueError("Only YouTube URLs are supported.")
        return value


VideoCreateRequest = Annotated[
    LocalFileVideoCreateRequest | ExternalUrlVideoCreateRequest,
    Field(discriminator="input_type"),
]


class VideoMutationRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    category: Category | None = None


class VideoCompleteRequest(BaseModel):
    etag: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)


class VideoCompleteResponse(BaseModel):
    video_id: UUID
    status: VideoStatus


class VideoResponse(BaseModel):
    video_id: UUID
    status: VideoStatus
    title: str
    category: Category
    input_type: InputType
    source_url: AnyHttpUrl | None = None
    failed_stage: FailedStage | None = None
    failure_code: str | None = None
    failure_trace_id: UUID | None = None
    storage_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LocalFileVideoCreateResponse(BaseModel):
    video_id: UUID
    status: Literal["PENDING"]
    signed_url: str
    expires_at: datetime
    upload_headers: dict[str, str] = Field(default_factory=dict)


class ExternalUrlVideoCreateResponse(BaseModel):
    video_id: UUID
    status: Literal["PENDING"]


class DeleteVideoResponse(BaseModel):
    video_id: UUID
    delete_requested: bool


class BatchDeleteVideosRequest(BaseModel):
    video_ids: list[UUID] = Field(min_length=1, max_length=100)


class BatchDeleteVideosResponse(BaseModel):
    video_ids: list[UUID]
    delete_requested: bool


class RetryVideoResponse(BaseModel):
    video_id: UUID
    status: Literal["PENDING"]


class PlaybackUrlResponse(BaseModel):
    signed_url: str
    expires_at: datetime


class VideoListResponse(BaseModel):
    items: list[VideoResponse]
    next_cursor: str | None


class ErrorResponse(BaseModel):
    code: str
    message: str
    trace_id: str
