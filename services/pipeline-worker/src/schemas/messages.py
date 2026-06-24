from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageType(str, Enum):
    PREPROCESS_REQUEST = "PREPROCESS_REQUEST"
    DELETE_REQUEST = "DELETE_REQUEST"


class ControlMessageType(str, Enum):
    TRAINING_REQUEST = "TRAINING_REQUEST"
    ROLLBACK_REQUEST = "ROLLBACK_REQUEST"


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    message_type: MessageType
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    video_id: UUID
    issued_at: datetime

    @property
    def is_preprocess(self) -> bool:
        return self.message_type == MessageType.PREPROCESS_REQUEST

    @property
    def is_delete(self) -> bool:
        return self.message_type == MessageType.DELETE_REQUEST


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
