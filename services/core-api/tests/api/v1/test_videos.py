from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from docker.errors import DockerException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.core.config import Settings
from src.infra.db.video_repository import VideoRepository
from src.infra.inmemory_broker import InMemoryBrokerClient
from src.infra.inmemory_storage import InMemoryStorageClient
from src.main import create_app
from src.models.video import Video


def to_asyncpg_url(connection_url: str) -> str:
    if connection_url.startswith("postgresql+psycopg2://"):
        return connection_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if connection_url.startswith("postgresql://"):
        return connection_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return connection_url


@pytest.fixture(scope="session")
def postgres_url() -> str:
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except DockerException as exc:
        pytest.skip(f"Docker is required for API video tests: {exc}")

    try:
        yield to_asyncpg_url(container.get_connection_url())
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_database(postgres_url: str) -> None:
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_url

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    command.upgrade(config, "head")

    yield

    command.downgrade(config, "base")
    if previous_database_url is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = previous_database_url


@pytest_asyncio.fixture
async def app_context(postgres_url: str, migrated_database: None):
    engine = create_async_engine(postgres_url, future=True)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE video"))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        gcp_project_id="project-id",
        gcs_video_bucket_name="video-bucket",
        jwt_secret_key="super-secret-key-that-is-at-least-32-bytes",
        database_url=postgres_url,
        broker_type="inmemory",
    )
    app = create_app(settings)
    app.state.container.db_session_factory = session_factory
    app.state.container.storage_client = InMemoryStorageClient()
    app.state.container.broker_client = InMemoryBrokerClient()

    try:
        yield app, settings, session_factory
    finally:
        await engine.dispose()


def create_token(secret: str, requester_user_id: str) -> str:
    payload = {
        "requester_user_id": requester_user_id,
        "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def seed_videos(session_factory, *videos: Video) -> None:
    async with session_factory() as session:
        repository = VideoRepository(session)
        for video in videos:
            await repository.add(video)
        await session.commit()


def build_video(
    *,
    user_id,
    status: str = "PENDING",
    input_type: str = "LOCAL_FILE",
    title: str = "Video",
    category: str = "GENERAL",
    created_at: datetime | None = None,
) -> Video:
    video_id = uuid4()
    kwargs = {}
    if created_at is not None:
        kwargs["created_at"] = created_at
        kwargs["updated_at"] = created_at

    return Video(
        id=video_id,
        user_id=user_id,
        title=title,
        category=category,
        input_type=input_type,
        source_url="https://example.com/watch?v=1" if input_type == "EXTERNAL_URL" else None,
        storage_path=f"videos/{user_id}/{video_id}/original.mp4",
        status=status,
        failed_stage="STT" if status == "FAILED" else None,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_get_videos_paginates_with_cursor_and_excludes_deleting(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
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
    await seed_videos(session_factory, ready_video, deleting_video, failed_video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        first_response = await client.get(
            "/api/v1/videos",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 1},
        )
        second_response = await client.get(
            "/api/v1/videos",
            headers={"Authorization": f"Bearer {token}"},
            params={"limit": 1, "cursor": first_response.json()["next_cursor"]},
        )

    assert first_response.status_code == 200
    assert [item["title"] for item in first_response.json()["items"]] == ["Newest visible"]
    assert first_response.json()["next_cursor"] is not None

    assert second_response.status_code == 200
    assert [item["title"] for item in second_response.json()["items"]] == ["Older visible"]
    assert second_response.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_videos_rejects_invalid_cursor(app_context) -> None:
    app, settings, _ = app_context
    token = create_token(settings.jwt_secret_key, str(uuid4()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/videos",
            headers={"Authorization": f"Bearer {token}"},
            params={"cursor": "not-a-valid-cursor"},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_get_videos_requires_authentication(app_context) -> None:
    app, _, _ = app_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get("/api/v1/videos")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.asyncio
async def test_get_video_returns_owned_video_metadata(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY", title="Owned video", category="IT")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.get(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["video_id"] == str(video.id)
    assert body["title"] == "Owned video"
    assert body["category"] == "IT"


@pytest.mark.asyncio
async def test_get_video_enforces_tenancy_and_missing_resource(app_context) -> None:
    app, settings, session_factory = app_context
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id)
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        forbidden_response = await client.get(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        not_found_response = await client.get(
            f"/api/v1/videos/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404


@pytest.mark.asyncio
async def test_patch_video_updates_title_and_category(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, title="Old title", category="GENERAL")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.patch(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "New title", "category": "MEDICAL"},
        )

    assert response.status_code == 200
    assert response.json()["title"] == "New title"
    assert response.json()["category"] == "MEDICAL"


@pytest.mark.asyncio
async def test_patch_video_rejects_deleting_status(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="DELETING")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.patch(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Blocked"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_patch_video_enforces_tenancy_missing_resource_and_validation(app_context) -> None:
    app, settings, session_factory = app_context
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id, status="READY")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        forbidden_response = await client.patch(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Blocked"},
        )
        not_found_response = await client.patch(
            f"/api/v1/videos/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
            json={"title": "Missing"},
        )
        invalid_response = await client.patch(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {create_token(settings.jwt_secret_key, str(owner_id))}"},
            json={"title": "x" * 256},
        )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404
    assert invalid_response.status_code == 400
    assert invalid_response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_delete_video_marks_deleting_and_publishes_delete_request(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.delete(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 202
    assert response.json() == {"video_id": str(video.id), "delete_requested": True}
    assert app.state.container.broker_client.published_messages[0]["message_type"] == "DELETE_REQUEST"

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id(video.id)

    assert stored_video is not None
    assert stored_video.status == "DELETING"


@pytest.mark.asyncio
async def test_delete_video_enforces_tenancy_and_missing_resource(app_context) -> None:
    app, settings, session_factory = app_context
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id)
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        forbidden_response = await client.delete(
            f"/api/v1/videos/{video.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        not_found_response = await client.delete(
            f"/api/v1/videos/{uuid4()}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404


@pytest.mark.asyncio
async def test_retry_video_requeues_failed_video(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="FAILED")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 202
    assert response.json() == {"video_id": str(video.id), "status": "PENDING"}
    assert app.state.container.broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id(video.id)

    assert stored_video is not None
    assert stored_video.status == "PENDING"
    assert stored_video.failed_stage == "STT"


@pytest.mark.asyncio
async def test_retry_video_rejects_non_failed_status(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "CONFLICT"


@pytest.mark.asyncio
async def test_retry_video_enforces_tenancy_and_missing_resource(app_context) -> None:
    app, settings, session_factory = app_context
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id, status="FAILED")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        forbidden_response = await client.post(
            f"/api/v1/videos/{video.id}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )
        not_found_response = await client.post(
            f"/api/v1/videos/{uuid4()}/retry",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404


@pytest.mark.asyncio
async def test_playback_url_returns_signed_url_for_ready_local_file(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=requester_user_id, status="READY")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/playback-url",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["signed_url"].endswith("?method=get")


@pytest.mark.asyncio
async def test_playback_url_rejects_non_ready_and_external_url_videos(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    pending_local = build_video(user_id=requester_user_id, status="PENDING")
    ready_external = build_video(user_id=requester_user_id, status="READY", input_type="EXTERNAL_URL")
    await seed_videos(session_factory, pending_local, ready_external)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        conflict_response = await client.post(
            f"/api/v1/videos/{pending_local.id}/playback-url",
            headers={"Authorization": f"Bearer {token}"},
        )
        bad_request_response = await client.post(
            f"/api/v1/videos/{ready_external.id}/playback-url",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert conflict_response.status_code == 409
    assert bad_request_response.status_code == 400


@pytest.mark.asyncio
async def test_playback_url_enforces_tenancy_and_missing_resource(app_context) -> None:
    app, settings, session_factory = app_context
    owner_id = UUID(str(uuid4()))
    requester_user_id = UUID(str(uuid4()))
    token = create_token(settings.jwt_secret_key, str(requester_user_id))
    video = build_video(user_id=owner_id, status="READY")
    await seed_videos(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        forbidden_response = await client.post(
            f"/api/v1/videos/{video.id}/playback-url",
            headers={"Authorization": f"Bearer {token}"},
        )
        not_found_response = await client.post(
            f"/api/v1/videos/{uuid4()}/playback-url",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert forbidden_response.status_code == 403
    assert not_found_response.status_code == 404
