from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
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
from src.infra.storage import MAX_UPLOAD_SIZE_BYTES
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
        pytest.skip(f"Docker is required for API complete tests: {exc}")

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


async def seed_video(session_factory, video: Video) -> None:
    async with session_factory() as session:
        repository = VideoRepository(session)
        await repository.add(video)
        await session.commit()


def build_video(*, user_id, status: str = "PENDING") -> Video:
    video_id = uuid4()
    return Video(
        id=video_id,
        user_id=user_id,
        title="Local upload",
        category="GENERAL",
        input_type="LOCAL_FILE",
        storage_path=f"videos/{user_id}/{video_id}/original.mp4",
        status=status,
    )


@pytest.mark.asyncio
async def test_post_complete_returns_202_and_marks_video_uploaded(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = str(uuid4())
    token = create_token(settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id))
    await seed_video(session_factory, video)
    app.state.container.storage_client.put_object(video.storage_path, b"video-bytes")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={"etag": "etag", "size_bytes": 11},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["video_id"] == str(video.id)
    assert body["status"] == "UPLOADED"
    assert app.state.container.broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(video.id, UUID(requester_user_id))

    assert stored_video is not None
    assert stored_video.status == "UPLOADED"


@pytest.mark.asyncio
async def test_post_complete_is_idempotent_for_uploaded_video(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = str(uuid4())
    token = create_token(settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id), status="UPLOADED")
    await seed_video(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "UPLOADED"
    assert app.state.container.broker_client.published_messages == []


@pytest.mark.asyncio
async def test_post_complete_rejects_missing_blob(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = str(uuid4())
    token = create_token(settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id))
    await seed_video(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_post_complete_rejects_oversized_blob(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = str(uuid4())
    token = create_token(settings.jwt_secret_key, requester_user_id)
    video = build_video(user_id=UUID(requester_user_id))
    await seed_video(session_factory, video)

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

    app.state.container.storage_client = OversizedStorage()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_post_complete_returns_403_for_other_users_video(app_context) -> None:
    app, settings, session_factory = app_context
    owner_id = str(uuid4())
    token = create_token(settings.jwt_secret_key, str(uuid4()))
    video = build_video(user_id=UUID(owner_id))
    await seed_video(session_factory, video)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{video.id}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_post_complete_returns_404_for_unknown_video(app_context) -> None:
    app, settings, _ = app_context
    token = create_token(settings.jwt_secret_key, str(uuid4()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            f"/api/v1/videos/{uuid4()}/complete",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
