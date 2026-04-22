from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas import ControlMessage, ControlMessageType, MessageEnvelope, MessageType
from src.infra.db.models import Base


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


def test_control_message_parses_training_request_without_video_id() -> None:
    payload = {
        "message_type": ControlMessageType.TRAINING_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
    }

    message = ControlMessage.model_validate(payload)

    assert message.message_type is ControlMessageType.TRAINING_REQUEST
    assert "video_id" not in message.model_dump()


def test_control_message_rejects_video_id_field() -> None:
    payload = {
        "message_type": ControlMessageType.ROLLBACK_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
        "video_id": str(uuid4()),
    }

    with pytest.raises(ValidationError):
        ControlMessage.model_validate(payload)


def test_admin_ops_foundation_models_match_shared_columns() -> None:
    tables = Base.metadata.tables

    assert "project_id" in tables["video"].columns
    assert "quality_metrics" in tables["model_evaluation"].columns
    assert "pass_criteria" in tables["model_evaluation"].columns
    assert "release_status" in tables["model_release"].columns
