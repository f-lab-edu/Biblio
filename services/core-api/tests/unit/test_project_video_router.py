from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import Response
from starlette.requests import Request

from src.api.v1.routers import videos
from src.middlewares.auth import AuthenticatedUser
from src.schemas.video_dto import LocalFileVideoCreateRequest, LocalFileVideoCreateResponse
from src.services.video_service import VideoActionResult


@pytest.mark.asyncio
async def test_project_video_create_passes_project_id_to_service(monkeypatch: pytest.MonkeyPatch) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    seen: dict[str, object] = {}

    class _FakeVideoService:
        def __init__(self, **kwargs: object) -> None:
            seen["dependencies"] = kwargs

        async def create_video(self, payload: object, **kwargs: object) -> VideoActionResult:
            # Async to satisfy the VideoService route dependency contract.
            seen["payload"] = payload
            seen["call"] = kwargs
            return VideoActionResult(
                payload=LocalFileVideoCreateResponse(
                    video_id=uuid4(),
                    status="PENDING",
                    signed_url="https://storage.local/upload.mp4",
                    expires_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
                ),
                status_code=201,
            )

    monkeypatch.setattr(videos, "VideoService", _FakeVideoService)
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
    response = Response()

    await videos.create_project_video(
        request=request,
        response=response,
        project_id=project_id,
        payload=LocalFileVideoCreateRequest(
            title="Project upload",
            category="GENERAL",
            input_type="LOCAL_FILE",
            extension=".mp4",
        ),
        user=AuthenticatedUser(requester_user_id=requester_user_id),
        db_session_factory=object(),
        storage_client=object(),
        broker_client=object(),
    )

    assert seen["call"]["project_id"] == project_id
    assert seen["call"]["requester_user_id"] == requester_user_id
    assert response.status_code == 201
