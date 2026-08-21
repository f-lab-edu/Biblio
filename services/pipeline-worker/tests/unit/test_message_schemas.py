from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas import (
    ControlMessage,
    ControlMessageType,
    EmbedBatchMessage,
    EnrichChunkMessage,
    MessageEnvelope,
    MessageType,
    NormalizeVideoMessage,
    TranscribePartMessage,
    parse_queue_message,
)
from src.infra.db.models import Base


def _base_payload(message_type: str) -> dict:
    return {
        "message_type": message_type,
        "payload_version": "v2",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "video_ids": [str(uuid4())],
        "issued_at": datetime.now(UTC).isoformat(),
    }


def test_envelope_parses_preprocess_request() -> None:
    payload = _base_payload(MessageType.PREPROCESS_REQUEST.value)
    envelope = MessageEnvelope.model_validate(payload)
    assert envelope.message_type is MessageType.PREPROCESS_REQUEST
    assert str(envelope.trace_id) == payload["trace_id"]
    assert [str(video_id) for video_id in envelope.video_ids] == payload["video_ids"]
    assert envelope.is_preprocess


def test_envelope_rejects_unknown_message_type() -> None:
    payload = _base_payload("UNKNOWN")
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(payload)


def test_envelope_requires_fields() -> None:
    payload = _base_payload(MessageType.DELETE_REQUEST.value)
    payload.pop("video_ids")
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(payload)


def test_envelope_rejects_legacy_video_id_field() -> None:
    payload = _base_payload(MessageType.DELETE_REQUEST.value)
    payload.pop("video_ids")
    payload["video_id"] = str(uuid4())
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(payload)


def test_envelope_parses_project_delete_request() -> None:
    payload = _base_payload(MessageType.PROJECT_DELETE_REQUEST.value)
    payload.pop("video_ids")
    payload["project_id"] = str(uuid4())

    envelope = MessageEnvelope.model_validate(payload)

    assert envelope.message_type is MessageType.PROJECT_DELETE_REQUEST
    assert str(envelope.project_id) == payload["project_id"]
    assert envelope.is_project_delete


class TestStageMessageSchemas:
    def test_parses_normalize_video_message(self) -> None:
        payload = {
            "message_type": "NORMALIZE_VIDEO",
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "pipeline_run_id": str(uuid4()),
            "video_id": str(uuid4()),
            "pipeline_version": "pipeline-v1",
            "issued_at": datetime.now(UTC).isoformat(),
        }

        message = parse_queue_message(payload)

        assert isinstance(message, NormalizeVideoMessage)
        assert message.message_type is MessageType.NORMALIZE_VIDEO

    def test_parses_transcribe_part_message(self) -> None:
        payload = {
            "message_type": "TRANSCRIBE_PART",
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "pipeline_run_id": str(uuid4()),
            "video_id": str(uuid4()),
            "audio_part_id": str(uuid4()),
            "part_index": 2,
            "stt_model_version": "chirp_2",
            "issued_at": datetime.now(UTC).isoformat(),
        }

        message = parse_queue_message(payload)

        assert isinstance(message, TranscribePartMessage)
        assert message.part_index == 2

    def test_parses_enrich_chunk_message(self) -> None:
        payload = {
            "message_type": "ENRICH_CHUNK",
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "pipeline_run_id": str(uuid4()),
            "video_id": str(uuid4()),
            "chunk_work_id": str(uuid4()),
            "chunk_index": 3,
            "chunking_version": "v1",
            "stt_model_version": "chirp_2",
            "issued_at": datetime.now(UTC).isoformat(),
        }

        message = parse_queue_message(payload)

        assert isinstance(message, EnrichChunkMessage)
        assert message.chunk_index == 3

    def test_parses_embed_batch_without_single_run_id(self) -> None:
        payload = {
            "message_type": "EMBED_BATCH",
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "batch_id": str(uuid4()),
            "embedding_model_version": "v001",
            "index_name": "video-chunks",
            "issued_at": datetime.now(UTC).isoformat(),
        }

        message = parse_queue_message(payload)

        assert isinstance(message, EmbedBatchMessage)
        assert message.pipeline_run_id is None

    def test_rejects_stage_message_missing_work_identifier(self) -> None:
        payload = {
            "message_type": "TRANSCRIBE_PART",
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "pipeline_run_id": str(uuid4()),
            "video_id": str(uuid4()),
            "part_index": 2,
            "stt_model_version": "chirp_2",
            "issued_at": datetime.now(UTC).isoformat(),
        }

        with pytest.raises(ValidationError):
            parse_queue_message(payload)


def test_project_delete_request_requires_project_id() -> None:
    payload = _base_payload(MessageType.PROJECT_DELETE_REQUEST.value)
    payload.pop("video_ids")

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


def test_rollback_control_message_requires_expected_active_release_fields() -> None:
    expected_switched_at = datetime.now(UTC)
    payload = {
        "message_type": ControlMessageType.ROLLBACK_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
        "expected_active_model_version": "model-v2",
        "expected_switched_at": expected_switched_at.isoformat(),
    }

    message = ControlMessage.model_validate(payload)

    assert message.expected_active_model_version == "model-v2"
    assert message.expected_switched_at == expected_switched_at


def test_rollback_control_message_rejects_missing_expected_active_release_fields() -> None:
    payload = {
        "message_type": ControlMessageType.ROLLBACK_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
    }

    with pytest.raises(ValidationError):
        ControlMessage.model_validate(payload)


def test_training_control_message_rejects_expected_active_release_fields() -> None:
    payload = {
        "message_type": ControlMessageType.TRAINING_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime.now(UTC).isoformat(),
        "expected_active_model_version": "model-v2",
        "expected_switched_at": datetime.now(UTC).isoformat(),
    }

    with pytest.raises(ValidationError):
        ControlMessage.model_validate(payload)


def test_admin_ops_foundation_models_match_shared_columns() -> None:
    tables = Base.metadata.tables

    assert "project_id" in tables["video"].columns
    assert "quality_metrics" in tables["model_evaluation"].columns
    assert "pass_criteria" in tables["model_evaluation"].columns
    assert "release_status" in tables["model_release"].columns
