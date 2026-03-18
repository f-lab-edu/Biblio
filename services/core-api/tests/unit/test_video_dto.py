from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from src.schemas.video_dto import (
    ExternalUrlVideoCreateRequest,
    LocalFileVideoCreateRequest,
    VideoCreateRequest,
    VideoMutationRequest,
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


def test_external_url_video_create_request_accepts_http_url() -> None:
    payload: Any = {
        "title": "Video title",
        "category": "GENERAL",
        "input_type": "EXTERNAL_URL",
        "source_url": "https://example.com/watch?v=1",
    }

    validated = video_create_adapter.validate_python(payload)

    assert isinstance(validated, ExternalUrlVideoCreateRequest)
    assert str(validated.source_url) == "https://example.com/watch?v=1"


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
