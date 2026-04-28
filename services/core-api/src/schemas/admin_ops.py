from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectSearchServingState(str, Enum):
    SERVABLE = "SERVABLE"
    ROLLBACK_EXCLUDED = "ROLLBACK_EXCLUDED"


class ReleaseStatus(str, Enum):
    STABLE = "STABLE"
    CANDIDATE_REINDEXING = "CANDIDATE_REINDEXING"
    ROLLBACK_PREPARING = "ROLLBACK_PREPARING"


class MLPipelineRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    READY_FOR_RELEASE = "READY_FOR_RELEASE"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"


class ControlMessageType(str, Enum):
    TRAINING_REQUEST = "TRAINING_REQUEST"
    ROLLBACK_REQUEST = "ROLLBACK_REQUEST"


class ControlMessage(BaseModel):
    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    message_type: ControlMessageType
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    issued_at: datetime
