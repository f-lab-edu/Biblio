from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MessageType(str, Enum):
    PREPROCESS_REQUEST = "PREPROCESS_REQUEST"
    DELETE_REQUEST = "DELETE_REQUEST"


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

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
