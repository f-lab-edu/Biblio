from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.runtime.messages import (
    DatasetGenerationRequest,
    ReembeddingRequest,
    build_dataset_generation_request,
    build_reembedding_request,
    build_training_request,
)


def test_dataset_generation_request_uses_dedicated_message_type() -> None:
    trace_id = uuid4()
    issued_at = datetime(2026, 5, 29, 3, 0, tzinfo=UTC)

    message = build_dataset_generation_request(trace_id=trace_id, issued_at=issued_at)

    assert message == {
        "message_type": "DATASET_GENERATION_REQUEST",
        "payload_version": "v1",
        "trace_id": str(trace_id),
        "attempt": 1,
        "issued_at": issued_at.isoformat(),
    }
    assert DatasetGenerationRequest.model_validate(message).trace_id == trace_id


def test_training_request_payload_does_not_include_dataset_version() -> None:
    payload = build_training_request(trace_id=uuid4(), issued_at=datetime(2026, 5, 29, 4, 0, tzinfo=UTC))

    assert payload["message_type"] == "TRAINING_REQUEST"
    assert "dataset_version" not in payload


def test_dataset_generation_request_rejects_wrong_message_type() -> None:
    with pytest.raises(ValidationError):
        DatasetGenerationRequest.model_validate(
            {
                "message_type": "TRAINING_REQUEST",
                "payload_version": "v1",
                "trace_id": str(uuid4()),
                "attempt": 1,
                "issued_at": datetime.now(UTC).isoformat(),
            }
        )


def test_build_reembedding_request_roundtrips():
    vid = uuid4()
    tid = uuid4()
    payload = build_reembedding_request(
        video_id=vid, target_model_version="v1", target_index_name="index-v1",
        trace_id=tid, issued_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    msg = ReembeddingRequest.model_validate(payload)
    assert msg.message_type == "REEMBEDDING_REQUEST"
    assert msg.payload_version == "v1"
    assert msg.video_id == vid
    assert msg.target_model_version == "v1"
    assert msg.target_index_name == "index-v1"
    assert msg.attempt == 1
