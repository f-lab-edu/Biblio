from datetime import datetime
from enum import Enum
from typing import Literal, TypeAlias
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageType(str, Enum):
    PREPROCESS_REQUEST = "PREPROCESS_REQUEST"
    DELETE_REQUEST = "DELETE_REQUEST"
    PROJECT_DELETE_REQUEST = "PROJECT_DELETE_REQUEST"
    NORMALIZE_VIDEO = "NORMALIZE_VIDEO"
    TRANSCRIBE_PART = "TRANSCRIBE_PART"
    ENRICH_CHUNK = "ENRICH_CHUNK"
    EMBED_BATCH = "EMBED_BATCH"


class ControlMessageType(str, Enum):
    TRAINING_REQUEST = "TRAINING_REQUEST"
    ROLLBACK_REQUEST = "ROLLBACK_REQUEST"


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    message_type: MessageType
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    video_ids: list[UUID] | None = Field(default=None, min_length=1)
    project_id: UUID | None = None
    issued_at: datetime

    @model_validator(mode="after")
    def validate_target_fields(self) -> "MessageEnvelope":
        if self.message_type in {
            MessageType.NORMALIZE_VIDEO,
            MessageType.TRANSCRIBE_PART,
            MessageType.ENRICH_CHUNK,
            MessageType.EMBED_BATCH,
        }:
            raise ValueError(
                f"{self.message_type.value} requires its stage-specific schema"
            )
        if self.message_type is MessageType.PROJECT_DELETE_REQUEST:
            if self.project_id is None:
                raise ValueError("PROJECT_DELETE_REQUEST requires project_id")
            if self.video_ids is not None:
                raise ValueError("PROJECT_DELETE_REQUEST does not accept video_ids")
            return self

        if self.video_ids is None:
            raise ValueError(f"{self.message_type.value} requires video_ids")
        if self.project_id is not None:
            raise ValueError(f"{self.message_type.value} does not accept project_id")
        return self

    @property
    def is_preprocess(self) -> bool:
        return self.message_type == MessageType.PREPROCESS_REQUEST

    @property
    def is_delete(self) -> bool:
        return self.message_type == MessageType.DELETE_REQUEST

    @property
    def is_project_delete(self) -> bool:
        return self.message_type == MessageType.PROJECT_DELETE_REQUEST


class StageMessageBase(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    message_type: MessageType
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    pipeline_run_id: UUID | None
    issued_at: datetime


class NormalizeVideoMessage(StageMessageBase):
    message_type: Literal[MessageType.NORMALIZE_VIDEO]
    pipeline_run_id: UUID
    video_id: UUID
    pipeline_version: str = Field(..., min_length=1)


class TranscribePartMessage(StageMessageBase):
    message_type: Literal[MessageType.TRANSCRIBE_PART]
    pipeline_run_id: UUID
    video_id: UUID
    audio_part_id: UUID
    part_index: int = Field(..., ge=0)
    stt_model_version: str = Field(..., min_length=1)


class EnrichChunkMessage(StageMessageBase):
    message_type: Literal[MessageType.ENRICH_CHUNK]
    pipeline_run_id: UUID
    video_id: UUID
    chunk_work_id: UUID
    chunk_index: int = Field(..., ge=0)
    chunking_version: str = Field(..., min_length=1)
    stt_model_version: str = Field(..., min_length=1)


class EmbedBatchMessage(StageMessageBase):
    message_type: Literal[MessageType.EMBED_BATCH]
    pipeline_run_id: None = None
    batch_id: UUID
    embedding_model_version: str = Field(..., min_length=1)
    index_name: str = Field(..., min_length=1)


StageMessage: TypeAlias = (
    NormalizeVideoMessage
    | TranscribePartMessage
    | EnrichChunkMessage
    | EmbedBatchMessage
)
QueueMessage: TypeAlias = MessageEnvelope | StageMessage

STAGE_MESSAGE_MODELS = {
    MessageType.NORMALIZE_VIDEO: NormalizeVideoMessage,
    MessageType.TRANSCRIBE_PART: TranscribePartMessage,
    MessageType.ENRICH_CHUNK: EnrichChunkMessage,
    MessageType.EMBED_BATCH: EmbedBatchMessage,
}


def parse_queue_message(payload: dict[str, object]) -> QueueMessage:
    message_type = MessageType(payload.get("message_type"))
    stage_model = STAGE_MESSAGE_MODELS.get(message_type)
    if stage_model is not None:
        return stage_model.model_validate(payload)
    return MessageEnvelope.model_validate(payload)


class ControlMessage(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    message_type: ControlMessageType
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    issued_at: datetime
    expected_active_model_version: str | None = None
    expected_switched_at: datetime | None = None

    @model_validator(mode="after")
    def validate_rollback_expected_release(self) -> "ControlMessage":
        has_expected_release = (
            self.expected_active_model_version is not None
            or self.expected_switched_at is not None
        )
        if self.message_type is ControlMessageType.ROLLBACK_REQUEST:
            if self.expected_active_model_version is None or self.expected_switched_at is None:
                raise ValueError(
                    "ROLLBACK_REQUEST requires expected_active_model_version and expected_switched_at"
                )
        elif has_expected_release:
            raise ValueError(
                "expected active release fields are only valid for ROLLBACK_REQUEST"
            )
        return self
