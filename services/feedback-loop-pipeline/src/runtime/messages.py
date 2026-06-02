from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DatasetGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_type: Literal["DATASET_GENERATION_REQUEST"]
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    issued_at: datetime
    source_window_start: datetime | None = None
    source_window_end: datetime | None = None


def build_dataset_generation_request(
    *,
    trace_id: UUID,
    issued_at: datetime | None = None,
    attempt: int = 1,
) -> dict[str, object]:
    issued = issued_at or datetime.now(UTC)
    return {
        "message_type": "DATASET_GENERATION_REQUEST",
        "payload_version": "v1",
        "trace_id": str(trace_id),
        "attempt": attempt,
        "issued_at": issued.isoformat(),
    }


def build_training_request(
    *,
    trace_id: UUID,
    issued_at: datetime | None = None,
    attempt: int = 1,
) -> dict[str, object]:
    issued = issued_at or datetime.now(UTC)
    return {
        "message_type": "TRAINING_REQUEST",
        "payload_version": "v1",
        "trace_id": str(trace_id),
        "attempt": attempt,
        "issued_at": issued.isoformat(),
    }


class ReembeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_type: Literal["REEMBEDDING_REQUEST"]
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(default=1, ge=1)
    issued_at: datetime
    video_id: UUID
    target_model_version: str
    target_index_name: str


def build_reembedding_request(
    *,
    video_id: UUID,
    target_model_version: str,
    target_index_name: str,
    trace_id: UUID,
    issued_at: datetime,
    attempt: int = 1,
) -> dict[str, object]:
    return {
        "message_type": "REEMBEDDING_REQUEST",
        "payload_version": "v1",
        "trace_id": str(trace_id),
        "attempt": attempt,
        "issued_at": issued_at.isoformat(),
        "video_id": str(video_id),
        "target_model_version": target_model_version,
        "target_index_name": target_index_name,
    }
