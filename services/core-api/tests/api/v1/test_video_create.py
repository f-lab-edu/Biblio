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
from src.main import create_app


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
        pytest.skip(f"Docker is required for API create tests: {exc}")

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


@pytest.mark.asyncio
async def test_post_videos_local_file_returns_201_and_persists_pending_video(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = str(uuid4())
    token = create_token(settings.jwt_secret_key, requester_user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/videos",
            headers={"Authorization": f"Bearer {token}"},
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

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(UUID(body["video_id"]), UUID(requester_user_id))

    assert stored_video is not None
    assert stored_video.status == "PENDING"


@pytest.mark.asyncio
async def test_post_videos_external_url_returns_202_and_publishes_message(app_context) -> None:
    app, settings, session_factory = app_context
    requester_user_id = str(uuid4())
    token = create_token(settings.jwt_secret_key, requester_user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/videos",
            headers={"Authorization": f"Bearer {token}"},
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
    assert app.state.container.broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(UUID(body["video_id"]), UUID(requester_user_id))

    assert stored_video is not None
    assert stored_video.source_url == "https://example.com/watch?v=1"


@pytest.mark.asyncio
async def test_post_videos_requires_authentication(app_context) -> None:
    app, _, _ = app_context

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
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
async def test_post_videos_rejects_invalid_extension(app_context) -> None:
    app, settings, _ = app_context
    token = create_token(settings.jwt_secret_key, str(uuid4()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/videos",
            headers={"Authorization": f"Bearer {token}"},
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
async def test_post_videos_returns_500_after_broker_retry_failure(app_context) -> None:
    app, settings, _ = app_context
    app.state.container.broker_client = InMemoryBrokerClient(failures_before_success=3)
    token = create_token(settings.jwt_secret_key, str(uuid4()))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/videos",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "title": "Broken broker",
                "category": "LEGAL",
                "input_type": "EXTERNAL_URL",
                "source_url": "https://example.com/fail",
            },
        )

    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
