from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    DEPLOY_COMPLETED = "DEPLOY_COMPLETED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"
    DEPLOYMENT_BLOCKED = "DEPLOYMENT_BLOCKED"


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
