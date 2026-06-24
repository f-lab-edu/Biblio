import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

VideoMessageType = Literal["PREPROCESS_REQUEST", "DELETE_REQUEST"]
ControlMessageType = Literal["TRAINING_REQUEST", "ROLLBACK_REQUEST"]
MessageType = VideoMessageType | ControlMessageType


class BrokerPublishError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BrokerMessage:
    message_type: VideoMessageType
    video_id: UUID
    trace_id: UUID
    attempt: int = 1
    payload_version: str = "v1"
    issued_at: datetime | None = None
    queue_name: str | None = None

    def to_payload(self) -> dict[str, object]:
        issued_at = self.issued_at or datetime.now(timezone.utc)
        return {
            "message_type": self.message_type,
            "payload_version": self.payload_version,
            "trace_id": str(self.trace_id),
            "attempt": self.attempt,
            "video_id": str(self.video_id),
            "issued_at": issued_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ControlBrokerMessage:
    message_type: ControlMessageType
    trace_id: UUID
    attempt: int = 1
    payload_version: str = "v1"
    issued_at: datetime | None = None
    queue_name: str | None = None
    expected_active_model_version: str | None = None
    expected_switched_at: datetime | None = None

    def to_payload(self) -> dict[str, object]:
        issued_at = self.issued_at or datetime.now(timezone.utc)
        payload: dict[str, object] = {
            "message_type": self.message_type,
            "payload_version": self.payload_version,
            "trace_id": str(self.trace_id),
            "attempt": self.attempt,
            "issued_at": issued_at.isoformat(),
        }
        if self.expected_active_model_version is not None:
            payload["expected_active_model_version"] = self.expected_active_model_version
        if self.expected_switched_at is not None:
            payload["expected_switched_at"] = self.expected_switched_at.isoformat()
        return payload


class PublishableMessage(Protocol):
    message_type: MessageType
    queue_name: str | None

    def to_payload(self) -> dict[str, object]: ...


def build_message(
    message_type: VideoMessageType,
    *,
    video_id: UUID,
    trace_id: UUID,
    attempt: int = 1,
    issued_at: datetime | None = None,
) -> BrokerMessage:
    return BrokerMessage(
        message_type=message_type,
        video_id=video_id,
        trace_id=trace_id,
        attempt=attempt,
        issued_at=issued_at,
    )


def build_control_message(
    message_type: ControlMessageType,
    *,
    trace_id: UUID,
    attempt: int = 1,
    issued_at: datetime | None = None,
    queue_name: str | None = None,
    expected_active_model_version: str | None = None,
    expected_switched_at: datetime | None = None,
) -> ControlBrokerMessage:
    return ControlBrokerMessage(
        message_type=message_type,
        trace_id=trace_id,
        attempt=attempt,
        issued_at=issued_at,
        queue_name=queue_name,
        expected_active_model_version=expected_active_model_version,
        expected_switched_at=expected_switched_at,
    )


class BrokerClient(ABC):
    @abstractmethod
    async def publish(self, message: PublishableMessage) -> int | None:
        raise NotImplementedError

    async def publish_with_retry(
        self,
        message: PublishableMessage,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.0,
    ) -> int | None:
        last_error: BrokerPublishError | None = None

        for attempt_number in range(1, max_attempts + 1):
            try:
                return await self.publish(message)
            except BrokerPublishError as exc:
                last_error = exc
                if attempt_number == max_attempts:
                    break
                if retry_delay_seconds > 0:
                    await asyncio.sleep(retry_delay_seconds)

        if last_error is None:
            raise BrokerPublishError("Publish retry loop exited without a result.")
        raise last_error
