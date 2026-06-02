from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas.admin_ops import ControlMessage, ControlMessageType, MLPipelineRunStatus
from src.schemas.feedback_dto import FeedbackEvent, FeedbackRating, FeedbackRequest


def test_control_message_accepts_training_request_without_video_id() -> None:
    payload = {
        "message_type": ControlMessageType.TRAINING_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime(2026, 4, 21, 12, 0, tzinfo=UTC).isoformat(),
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
        "issued_at": datetime(2026, 4, 21, 12, 0, tzinfo=UTC).isoformat(),
        "video_id": str(uuid4()),
    }

    with pytest.raises(ValidationError):
        ControlMessage.model_validate(payload)


def test_rollback_control_message_requires_expected_active_release_fields() -> None:
    payload = {
        "message_type": ControlMessageType.ROLLBACK_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime(2026, 4, 21, 12, 0, tzinfo=UTC).isoformat(),
        "expected_active_model_version": "model-v2",
        "expected_switched_at": datetime(2026, 4, 21, 11, 0, tzinfo=UTC).isoformat(),
    }

    message = ControlMessage.model_validate(payload)

    assert message.expected_active_model_version == "model-v2"
    assert message.expected_switched_at == datetime(2026, 4, 21, 11, 0, tzinfo=UTC)


def test_rollback_control_message_rejects_missing_expected_active_release_fields() -> None:
    payload = {
        "message_type": ControlMessageType.ROLLBACK_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime(2026, 4, 21, 12, 0, tzinfo=UTC).isoformat(),
    }

    with pytest.raises(ValidationError):
        ControlMessage.model_validate(payload)


def test_training_control_message_rejects_expected_active_release_fields() -> None:
    payload = {
        "message_type": ControlMessageType.TRAINING_REQUEST.value,
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": datetime(2026, 4, 21, 12, 0, tzinfo=UTC).isoformat(),
        "expected_active_model_version": "model-v2",
        "expected_switched_at": datetime(2026, 4, 21, 11, 0, tzinfo=UTC).isoformat(),
    }

    with pytest.raises(ValidationError):
        ControlMessage.model_validate(payload)


def test_feedback_event_preserves_snapshot_context_fields() -> None:
    chunk_id = uuid4()
    payload = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "user_id": str(uuid4()),
        "project_id": str(uuid4()),
        "req_id": str(uuid4()),
        "query_text": "find key point",
        "rating": FeedbackRating.LIKE.value,
        "topk_ids": [str(chunk_id)],
        "used_ids": [str(chunk_id)],
        "active_model_version": "embedding-v1",
        "active_index_name": "active-index",
        "response_snapshot_ref": "snapshot/ref",
        "created_at": datetime(2026, 4, 21, 12, 0, tzinfo=UTC).isoformat(),
        "trace_id": str(uuid4()),
    }

    event = FeedbackEvent.model_validate(payload)

    assert event.schema_version == 1
    assert event.rating is FeedbackRating.LIKE
    assert event.topk_ids == [chunk_id]
    assert event.used_ids == [chunk_id]


def test_feedback_request_accepts_only_public_fields() -> None:
    req_id = uuid4()
    request = FeedbackRequest.model_validate(
        {
            "req_id": str(req_id),
            "rating": FeedbackRating.DISLIKE.value,
        }
    )

    assert request.req_id == req_id
    assert request.rating is FeedbackRating.DISLIKE


def test_feedback_request_rejects_internal_event_fields() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {
                "req_id": str(uuid4()),
                "rating": FeedbackRating.LIKE.value,
                "user_id": str(uuid4()),
            }
        )


def test_ml_run_status_values_match_foundation_contract() -> None:
    assert {status.value for status in MLPipelineRunStatus} == {
        "PENDING",
        "RUNNING",
        "READY_FOR_RELEASE",
        "FAILED",
        "SUPERSEDED",
        "DEPLOYMENT_BLOCKED",
    }
