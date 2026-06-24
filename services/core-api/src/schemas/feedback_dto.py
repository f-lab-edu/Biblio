from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FeedbackRating(StrEnum):
    LIKE = "LIKE"
    DISLIKE = "DISLIKE"


class ServedVectorPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str = Field(..., min_length=1)
    index_name: str = Field(..., min_length=1)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    req_id: UUID
    rating: FeedbackRating


class FeedbackEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    schema_version: int = Field(1, ge=1)
    event_id: UUID
    user_id: UUID
    project_id: UUID
    req_id: UUID
    query_text: str = Field(..., min_length=1)
    rating: FeedbackRating
    topk_ids: list[UUID]
    used_ids: list[UUID]
    active_model_version: str = Field(..., min_length=1)
    active_index_name: str = Field(..., min_length=1)
    response_snapshot_ref: str = Field(..., min_length=1)
    created_at: datetime
    trace_id: UUID | None = None
    served_vector_paths: list[ServedVectorPath] = Field(default_factory=list)
