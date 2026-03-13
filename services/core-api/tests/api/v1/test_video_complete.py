from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.infra.db.video_repository import VideoRepository
from src.infra.storage import MAX_UPLOAD_SIZE_BYTES
from src.models.video import Video
from tests.support import AppContext, auth_headers, build_video, create_token, seed_video


@pytest.mark.asyncio
async def test_post_complete_returns_202_and_marks_video_uploaded(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = str(uuid4())
    token = create_token(app_context.settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id))
    await seed_video(app_context.session_factory, video)
    app_context.app.state.container.storage_client.put_object(video.storage_path, b"video-bytes")
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/complete",
        headers=auth_headers(token),
        json={"etag": "etag", "size_bytes": 11},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["video_id"] == str(video.id)
    assert body["status"] == "UPLOADED"
    assert app_context.app.state.container.broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(video.id, UUID(requester_user_id))

    assert stored_video is not None
    assert stored_video.status == "UPLOADED"


@pytest.mark.asyncio
async def test_post_complete_is_idempotent_for_uploaded_video(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = str(uuid4())
    token = create_token(app_context.settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id), status="UPLOADED")
    await seed_video(app_context.session_factory, video)
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/complete",
        headers=auth_headers(token),
        json={},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "UPLOADED"
    assert app_context.app.state.container.broker_client.published_messages == []


@pytest.mark.asyncio
async def test_post_complete_rejects_missing_blob(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = str(uuid4())
    token = create_token(app_context.settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id))
    await seed_video(app_context.session_factory, video)
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/complete",
        headers=auth_headers(token),
        json={},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_post_complete_rejects_oversized_blob(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = str(uuid4())
    token = create_token(app_context.settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id))
    await seed_video(app_context.session_factory, video)

    class OversizedStorage:
        def generate_signed_url(self, request):
            raise AssertionError("generate_signed_url should not be called")

        def delete_object(self, object_name: str) -> bool:
            return True

        def get_blob_metadata(self, object_name: str):
            return type(
                "BlobMetadataLike",
                (),
                {"exists": True, "size_bytes": MAX_UPLOAD_SIZE_BYTES + 1, "etag": "etag"},
            )()

    app_context.app.state.container.storage_client = OversizedStorage()
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/complete",
        headers=auth_headers(token),
        json={},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_post_complete_returns_403_for_other_users_video(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    owner_id = str(uuid4())
    token = create_token(app_context.settings.jwt_secret_key, str(uuid4()))
    video = build_video(user_id=UUID(owner_id))
    await seed_video(app_context.session_factory, video)
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/complete",
        headers=auth_headers(token),
        json={},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_post_complete_returns_404_for_unknown_video(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    token = create_token(app_context.settings.jwt_secret_key, str(uuid4()))
    response = await api_client.post(
        f"/api/v1/videos/{uuid4()}/complete",
        headers=auth_headers(token),
        json={},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
