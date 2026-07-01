from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.infra.db.video_repository import VideoRepository
from src.infra.inmemory_broker import InMemoryBrokerClient
from tests.support import AppContext, auth_headers, build_video, create_token, seed_videos


@pytest.mark.asyncio
async def test_get_videos_paginates_with_cursor_and_excludes_deleting(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    base_time = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    ready_video = build_video(
        user_id=requester_user_id,
        status="READY",
        title="Newest visible",
        created_at=base_time + timedelta(minutes=2),
    )
    deleting_video = build_video(
        user_id=requester_user_id,
        status="DELETING",
        title="Hidden deleting",
        created_at=base_time + timedelta(minutes=1),
    )
    failed_video = build_video(
        user_id=requester_user_id,
        status="FAILED",
        title="Older visible",
        created_at=base_time,
    )
    await seed_videos(app_context.session_factory, ready_video, deleting_video, failed_video)

    first_response = await api_client.get(
        "/api/v1/videos",
        headers=auth_headers(token),
        params={"limit": 1},
    )
    second_response = await api_client.get(
        "/api/v1/videos",
        headers=auth_headers(token),
        params={"limit": 1, "cursor": first_response.json()["next_cursor"]},
    )

    assert first_response.status_code == 200
    assert [item["title"] for item in first_response.json()["items"]] == ["Newest visible"]
    assert first_response.json()["next_cursor"] is not None

    assert second_response.status_code == 200
    assert [item["title"] for item in second_response.json()["items"]] == ["Older visible"]
    assert second_response.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_videos_rejects_invalid_cursor(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    token = create_token(app_context.settings.jwt_secret_key, str(uuid4()))
    response = await api_client.get(
        "/api/v1/videos",
        headers=auth_headers(token),
        params={"cursor": "not-a-valid-cursor"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_get_videos_requires_authentication(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/videos")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_get_video_returns_owned_video_metadata(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY", title="Owned video", category="IT")
    await seed_videos(app_context.session_factory, video)
    response = await api_client.get(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == str(video.id)
    assert body["title"] == "Owned video"
    assert body["category"] == "IT"


@pytest.mark.asyncio
async def test_get_video_enforces_tenancy_and_missing_resource(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id)
    await seed_videos(app_context.session_factory, video)

    forbidden_response = await api_client.get(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
    )
    not_found_response = await api_client.get(
        f"/api/v1/videos/{uuid4()}",
        headers=auth_headers(token),
    )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404


@pytest.mark.asyncio
async def test_patch_video_updates_title_and_category(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, title="Old title", category="GENERAL")
    await seed_videos(app_context.session_factory, video)
    response = await api_client.patch(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
        json={"title": "New title", "category": "MEDICAL"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "New title"
    assert response.json()["category"] == "MEDICAL"


@pytest.mark.asyncio
async def test_patch_video_rejects_deleting_status(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="DELETING")
    await seed_videos(app_context.session_factory, video)
    response = await api_client.patch(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
        json={"title": "Blocked"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_patch_video_enforces_tenancy_missing_resource_and_validation(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id, status="READY")
    await seed_videos(app_context.session_factory, video)

    forbidden_response = await api_client.patch(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
        json={"title": "Blocked"},
    )
    not_found_response = await api_client.patch(
        f"/api/v1/videos/{uuid4()}",
        headers=auth_headers(token),
        json={"title": "Missing"},
    )
    invalid_response = await api_client.patch(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(create_token(app_context.settings.jwt_secret_key, str(owner_id))),
        json={"title": "x" * 256},
    )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404
    assert invalid_response.status_code == 400
    assert invalid_response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_delete_video_marks_deleting_and_publishes_delete_request(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY")
    await seed_videos(app_context.session_factory, video)
    response = await api_client.delete(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 202
    assert response.json() == {"video_id": str(video.id), "delete_requested": True}
    assert app_context.app.state.container.broker_client.published_messages[0]["message_type"] == "DELETE_REQUEST"
    assert app_context.app.state.container.broker_client.published_messages[0]["video_ids"] == [str(video.id)]

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id(video.id)

    assert stored_video is not None
    assert stored_video.status == "DELETING"


@pytest.mark.asyncio
async def test_batch_delete_videos_marks_all_deleting_and_publishes_one_request(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    first_video = build_video(user_id=requester_user_id, status="READY")
    second_video = build_video(user_id=requester_user_id, status="FAILED")
    await seed_videos(app_context.session_factory, first_video, second_video)

    response = await api_client.post(
        "/api/v1/videos:batch-delete",
        headers=auth_headers(token),
        json={"video_ids": [str(first_video.id), str(second_video.id)]},
    )

    assert response.status_code == 202
    assert response.json() == {
        "video_ids": [str(first_video.id), str(second_video.id)],
        "delete_requested": True,
    }
    assert app_context.app.state.container.broker_client.published_messages == [
        {
            "message_type": "DELETE_REQUEST",
            "payload_version": "v2",
            "trace_id": app_context.app.state.container.broker_client.published_messages[0]["trace_id"],
            "attempt": 1,
            "video_ids": [str(first_video.id), str(second_video.id)],
            "issued_at": app_context.app.state.container.broker_client.published_messages[0]["issued_at"],
        }
    ]

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_first = await repository.get_by_id(first_video.id)
        stored_second = await repository.get_by_id(second_video.id)

    assert stored_first is not None
    assert stored_first.status == "DELETING"
    assert stored_second is not None
    assert stored_second.status == "DELETING"


@pytest.mark.asyncio
async def test_delete_video_enforces_tenancy_and_missing_resource(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id)
    await seed_videos(app_context.session_factory, video)

    forbidden_response = await api_client.delete(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
    )
    not_found_response = await api_client.delete(
        f"/api/v1/videos/{uuid4()}",
        headers=auth_headers(token),
    )

    assert forbidden_response.status_code == 404
    assert not_found_response.status_code == 404


@pytest.mark.asyncio
async def test_batch_delete_videos_rejects_foreign_video_without_changes(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    owner_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    owned_video = build_video(user_id=requester_user_id, status="READY")
    foreign_video = build_video(user_id=owner_id, status="READY")
    await seed_videos(app_context.session_factory, owned_video, foreign_video)

    response = await api_client.post(
        "/api/v1/videos:batch-delete",
        headers=auth_headers(token),
        json={"video_ids": [str(owned_video.id), str(foreign_video.id)]},
    )

    assert response.status_code == 404
    assert app_context.app.state.container.broker_client.published_messages == []

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_owned = await repository.get_by_id(owned_video.id)
        stored_foreign = await repository.get_by_id(foreign_video.id)

    assert stored_owned is not None
    assert stored_owned.status == "READY"
    assert stored_foreign is not None
    assert stored_foreign.status == "READY"


@pytest.mark.asyncio
async def test_delete_video_restores_status_when_broker_publish_fails(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY")
    await seed_videos(app_context.session_factory, video)
    app_context.app.state.container.broker_client = InMemoryBrokerClient(failures_before_success=3)

    response = await api_client.delete(
        f"/api/v1/videos/{video.id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 500

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id(video.id)

    assert stored_video is not None
    assert stored_video.status == "READY"


@pytest.mark.asyncio
async def test_retry_video_requeues_failed_video(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="FAILED")
    await seed_videos(app_context.session_factory, video)
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/retry",
        headers=auth_headers(token),
    )

    assert response.status_code == 202
    assert response.json() == {"video_id": str(video.id), "status": "PENDING"}
    assert app_context.app.state.container.broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"

    async with app_context.session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id(video.id)

    assert stored_video is not None
    assert stored_video.status == "PENDING"
    assert stored_video.failed_stage == "STT"


@pytest.mark.asyncio
async def test_retry_video_rejects_non_failed_status(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY")
    await seed_videos(app_context.session_factory, video)
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/retry",
        headers=auth_headers(token),
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_retry_video_enforces_tenancy_and_missing_resource(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id, status="FAILED")
    await seed_videos(app_context.session_factory, video)

    forbidden_response = await api_client.post(
        f"/api/v1/videos/{video.id}/retry",
        headers=auth_headers(token),
    )
    not_found_response = await api_client.post(
        f"/api/v1/videos/{uuid4()}/retry",
        headers=auth_headers(token),
    )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404


@pytest.mark.asyncio
async def test_playback_url_returns_signed_url_for_ready_local_file(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY")
    await seed_videos(app_context.session_factory, video)
    response = await api_client.post(
        f"/api/v1/videos/{video.id}/playback-url",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["signed_url"].endswith("?method=get")


@pytest.mark.asyncio
async def test_playback_url_rejects_non_ready_and_external_url_videos(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    pending_local = build_video(user_id=requester_user_id, status="PENDING")
    ready_external = build_video(user_id=requester_user_id, status="READY", input_type="EXTERNAL_URL")
    await seed_videos(app_context.session_factory, pending_local, ready_external)

    conflict_response = await api_client.post(
        f"/api/v1/videos/{pending_local.id}/playback-url",
        headers=auth_headers(token),
    )
    bad_request_response = await api_client.post(
        f"/api/v1/videos/{ready_external.id}/playback-url",
        headers=auth_headers(token),
    )

    assert conflict_response.status_code == 409
    assert bad_request_response.status_code == 400


@pytest.mark.asyncio
async def test_playback_url_enforces_tenancy_and_missing_resource(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id, status="READY")
    await seed_videos(app_context.session_factory, video)

    forbidden_response = await api_client.post(
        f"/api/v1/videos/{video.id}/playback-url",
        headers=auth_headers(token),
    )
    not_found_response = await api_client.post(
        f"/api/v1/videos/{uuid4()}/playback-url",
        headers=auth_headers(token),
    )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404
