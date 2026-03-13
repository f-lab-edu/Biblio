from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from docker.errors import DockerException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.infra.db.video_repository import VideoRepository
from src.infra.inmemory_broker import InMemoryBrokerClient
from src.infra.inmemory_storage import InMemoryStorageClient
from src.infra.storage import MAX_UPLOAD_SIZE_BYTES
from src.middlewares.error_handler import ApiError
from src.schemas.video_dto import ExternalUrlVideoCreateRequest, LocalFileVideoCreateRequest
from src.services.video_service import VideoService


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
        pytest.skip(f"Docker is required for service upload tests: {exc}")

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
async def session_factory(postgres_url: str, migrated_database: None):
    engine = create_async_engine(postgres_url, future=True)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE video"))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_video_local_file_returns_signed_url_and_persists_pending_video(session_factory) -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    storage_client = InMemoryStorageClient(now_provider=lambda: now)
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    requester_user_id = uuid4()

    result = await service.create_video(
        LocalFileVideoCreateRequest(
            title="Local video",
            category="GENERAL",
            input_type="LOCAL_FILE",
            extension=".mp4",
        ),
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )

    assert result.status_code == 201
    assert result.payload.status == "PENDING"
    assert result.payload.signed_url.endswith(".mp4?method=put")
    assert storage_client.generated_requests[0].max_size_bytes == MAX_UPLOAD_SIZE_BYTES

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(result.payload.video_id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "PENDING"
    assert stored_video.storage_path == f"videos/{requester_user_id}/{result.payload.video_id}/original.mp4"
    assert broker_client.published_messages == []


@pytest.mark.asyncio
async def test_create_video_external_url_publishes_preprocess_request(session_factory) -> None:
    storage_client = InMemoryStorageClient()
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    requester_user_id = uuid4()
    trace_id = uuid4()

    result = await service.create_video(
        ExternalUrlVideoCreateRequest(
            title="External video",
            category="IT",
            input_type="EXTERNAL_URL",
            source_url="https://example.com/watch?v=1",
        ),
        requester_user_id=requester_user_id,
        trace_id=trace_id,
    )

    assert result.status_code == 202
    assert result.payload.status == "PENDING"
    assert broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"
    assert broker_client.published_messages[0]["trace_id"] == str(trace_id)
    assert broker_client.published_messages[0]["video_id"] == str(result.payload.video_id)

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(result.payload.video_id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "PENDING"
    assert stored_video.source_url == "https://example.com/watch?v=1"
    assert stored_video.storage_path == f"videos/{requester_user_id}/{result.payload.video_id}/original"


@pytest.mark.asyncio
async def test_create_video_external_url_raises_500_after_broker_retries(session_factory) -> None:
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(failures_before_success=3),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.create_video(
            ExternalUrlVideoCreateRequest(
                title="Broken external video",
                category="LEGAL",
                input_type="EXTERNAL_URL",
                source_url="https://example.com/broken",
            ),
            requester_user_id=uuid4(),
            trace_id=uuid4(),
        )

    assert "publish failed after retries" in str(exc_info.value).lower()
