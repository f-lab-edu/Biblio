from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from schemas import MessageEnvelope, MessageType


def _base_payload(message_type: str) -> dict:
    return {
        "message_type": message_type,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "video_id": str(uuid4()),
        "issued_at": datetime.now(UTC).isoformat(),
    }


def test_envelope_parses_preprocess_request() -> None:
    payload = _base_payload(MessageType.PREPROCESS_REQUEST.value)
    envelope = MessageEnvelope.model_validate(payload)
    assert envelope.message_type is MessageType.PREPROCESS_REQUEST
    assert str(envelope.trace_id) == payload["trace_id"]
    assert envelope.is_preprocess


def test_envelope_rejects_unknown_message_type() -> None:
    payload = _base_payload("UNKNOWN")
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(payload)


def test_envelope_requires_fields() -> None:
    payload = _base_payload(MessageType.DELETE_REQUEST.value)
    payload.pop("video_id")
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(payload)
