from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.infra.db.video_repository import VideoRepository
from src.infra.inmemory_broker import InMemoryBrokerClient
from tests.support import AppContext, auth_headers, create_token


@pytest.mark.asyncio
async def test_post_videos_local_file_returns_201_and_persists_pending_video(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = str(uuid4())
    token = create_token(app_context.settings.jwt_secret_key, requester_user_id)
    response = await api_client.post(
        "/api/v1/videos",
        headers=auth_headers(token),
        json={
            "title": "Local upload",
            "category": "GENERAL",
            "input_type": "LOCAL_FILE",
            "extension": ".mp4",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING"
    assert body["signed_url"].endswith(".mp4?method=put")

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(UUID(body["video_id"]), UUID(requester_user_id))

    assert stored_video is not None
    assert stored_video.status == "PENDING"


@pytest.mark.asyncio
async def test_post_videos_external_url_returns_202_and_publishes_message(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = str(uuid4())
    token = create_token(app_context.settings.jwt_secret_key, requester_user_id)
    response = await api_client.post(
        "/api/v1/videos",
        headers=auth_headers(token),
        json={
            "title": "External upload",
            "category": "IT",
            "input_type": "EXTERNAL_URL",
            "source_url": "https://example.com/watch?v=1",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "PENDING"
    assert app_context.app.state.container.broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(UUID(body["video_id"]), UUID(requester_user_id))

    assert stored_video is not None
    assert stored_video.source_url == "https://example.com/watch?v=1"


@pytest.mark.asyncio
async def test_post_videos_requires_authentication(
    api_client: AsyncClient,
) -> None:
    response = await api_client.post(
        "/api/v1/videos",
        json={
            "title": "No auth",
            "category": "GENERAL",
            "input_type": "LOCAL_FILE",
            "extension": ".mp4",
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_post_videos_rejects_invalid_extension(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    token = create_token(app_context.settings.jwt_secret_key, str(uuid4()))
    response = await api_client.post(
        "/api/v1/videos",
        headers=auth_headers(token),
        json={
            "title": "Bad extension",
            "category": "GENERAL",
            "input_type": "LOCAL_FILE",
            "extension": ".exe",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_post_videos_returns_500_after_broker_retry_failure(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    app_context.app.state.container.broker_client = InMemoryBrokerClient(failures_before_success=3)
    token = create_token(app_context.settings.jwt_secret_key, str(uuid4()))
    response = await api_client.post(
        "/api/v1/videos",
        headers=auth_headers(token),
        json={
            "title": "Broken broker",
            "category": "LEGAL",
            "input_type": "EXTERNAL_URL",
            "source_url": "https://example.com/fail",
        },
    )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
