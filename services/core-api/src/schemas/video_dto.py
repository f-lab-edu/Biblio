from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field

Category = Literal["GENERAL", "IT", "MEDICAL", "LEGAL"]
InputType = Literal["LOCAL_FILE", "EXTERNAL_URL"]
VideoStatus = Literal["PENDING", "UPLOADED", "PROCESSING", "READY", "FAILED", "DELETING"]
FailedStage = Literal["DOWNLOAD", "EXTRACT", "STT", "CHUNKING", "EMBEDDING", "VECTOR_UPSERT"]
FileExtension = Literal[".mp4", ".webm", ".mov", ".mkv", ".avi", ".wmv"]


class VideoCreateBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    category: Category


class LocalFileVideoCreateRequest(VideoCreateBase):
    input_type: Literal["LOCAL_FILE"]
    extension: FileExtension


class ExternalUrlVideoCreateRequest(VideoCreateBase):
    input_type: Literal["EXTERNAL_URL"]
    source_url: AnyHttpUrl


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
    storage_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class LocalFileVideoCreateResponse(BaseModel):
    video_id: UUID
    status: Literal["PENDING"]
    signed_url: str
    expires_at: datetime


class ExternalUrlVideoCreateResponse(BaseModel):
    video_id: UUID
    status: Literal["PENDING"]


class DeleteVideoResponse(BaseModel):
    video_id: UUID
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
