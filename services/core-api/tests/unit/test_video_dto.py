from typing import Any
from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from src.schemas.video_dto import (
    ExternalUrlVideoCreateRequest,
    LocalFileVideoCreateRequest,
    VideoCreateRequest,
    VideoMutationRequest,
    VideoResponse,
)

video_create_adapter = TypeAdapter(VideoCreateRequest)


def test_local_file_video_create_request_accepts_supported_extension() -> None:
    payload: Any = {
        "title": "Video title",
        "category": "IT",
        "input_type": "LOCAL_FILE",
        "extension": ".mp4",
    }

    validated = video_create_adapter.validate_python(payload)

    assert isinstance(validated, LocalFileVideoCreateRequest)
    assert validated.extension == ".mp4"


def test_local_file_video_create_request_normalizes_uppercase_extension() -> None:
    payload: Any = {
        "title": "Video title",
        "category": "IT",
        "input_type": "LOCAL_FILE",
        "extension": ".MP4",
    }

    validated = video_create_adapter.validate_python(payload)

    assert isinstance(validated, LocalFileVideoCreateRequest)
    assert validated.extension == ".mp4"


def test_local_file_video_create_request_marks_unsupported_extension() -> None:
    payload: Any = {
        "title": "Video title",
        "category": "IT",
        "input_type": "LOCAL_FILE",
        "extension": ".exe",
    }

    with pytest.raises(ValidationError) as exc_info:
        video_create_adapter.validate_python(payload)

    assert exc_info.value.errors()[0]["type"] == "unsupported_file_type"


def test_external_url_video_create_request_accepts_youtube_url() -> None:
    payload: Any = {
        "title": "Video title",
        "category": "GENERAL",
        "input_type": "EXTERNAL_URL",
        "source_url": "https://www.youtube.com/watch?v=1",
    }

    validated = video_create_adapter.validate_python(payload)

    assert isinstance(validated, ExternalUrlVideoCreateRequest)
    assert str(validated.source_url) == "https://www.youtube.com/watch?v=1"


def test_external_url_video_create_request_rejects_non_youtube_url() -> None:
    payload: Any = {
        "title": "Video title",
        "category": "GENERAL",
        "input_type": "EXTERNAL_URL",
        "source_url": "https://example.com/watch?v=1",
    }

    with pytest.raises(ValidationError):
        video_create_adapter.validate_python(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "title": "Video title",
            "category": "UNKNOWN",
            "input_type": "LOCAL_FILE",
            "extension": ".mp4",
        },
        {
            "title": "Video title",
            "category": "GENERAL",
            "input_type": "EXTERNAL_URL",
        },
        {
            "title": "Video title",
            "category": "GENERAL",
            "input_type": "LOCAL_FILE",
            "extension": ".exe",
        },
    ],
)
def test_video_create_request_rejects_invalid_payload(payload: Any) -> None:
    with pytest.raises(ValidationError):
        video_create_adapter.validate_python(payload)


def test_video_mutation_request_allows_partial_updates() -> None:
    payload = VideoMutationRequest(title="Updated title")

    assert payload.title == "Updated title"
    assert payload.category is None


def test_video_response_includes_failure_metadata() -> None:
    failure_trace_id = uuid4()

    payload = VideoResponse(
        video_id=uuid4(),
        status="FAILED",
        title="Failed video",
        category="GENERAL",
        input_type="LOCAL_FILE",
        failed_stage="STT",
        failure_code="STT_FAILED",
        failure_trace_id=failure_trace_id,
    )

    assert payload.failure_code == "STT_FAILED"
    assert payload.failure_trace_id == failure_trace_id


def test_video_response_allows_missing_failure_metadata() -> None:
    payload = VideoResponse(
        video_id=uuid4(),
        status="READY",
        title="Ready video",
        category="GENERAL",
        input_type="LOCAL_FILE",
    )

    assert payload.failed_stage is None
    assert payload.failure_code is None
    assert payload.failure_trace_id is None
