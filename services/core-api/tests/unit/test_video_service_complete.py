from __future__ import annotations

import os
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
from src.middlewares.error_handler import ApiError, ForbiddenError, InvalidArgumentError, NotFoundError
from src.models.video import Video
from src.schemas.video_dto import VideoCompleteRequest
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
        pytest.skip(f"Docker is required for service complete tests: {exc}")

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


async def seed_video(session_factory, video: Video) -> None:
    async with session_factory() as session:
        repository = VideoRepository(session)
        await repository.add(video)
        await session.commit()


def build_video(*, user_id, status: str = "PENDING", input_type: str = "LOCAL_FILE") -> Video:
    video_id = uuid4()
    storage_path = f"videos/{user_id}/{video_id}/original.mp4"
    return Video(
        id=video_id,
        user_id=user_id,
        title="Local upload",
        category="GENERAL",
        input_type=input_type,
        source_url="https://example.com/watch?v=1" if input_type == "EXTERNAL_URL" else None,
        storage_path=storage_path,
        status=status,
        failed_stage="STT" if status == "FAILED" else None,
    )


@pytest.mark.asyncio
async def test_complete_video_transitions_pending_local_file_and_publishes(session_factory) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    storage_client = InMemoryStorageClient()
    storage_client.put_object(video.storage_path, b"video-bytes", etag="blob-etag")
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    trace_id = uuid4()

    result = await service.complete_video(
        video.id,
        VideoCompleteRequest(etag="blob-etag", size_bytes=11),
        requester_user_id=requester_user_id,
        trace_id=trace_id,
    )

    assert result.status_code == 202
    assert result.payload.video_id == video.id
    assert result.payload.status == "UPLOADED"
    assert len(broker_client.published_messages) == 1
    assert broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"
    assert broker_client.published_messages[0]["payload_version"] == "v1"
    assert broker_client.published_messages[0]["trace_id"] == str(trace_id)
    assert broker_client.published_messages[0]["attempt"] == 1
    assert broker_client.published_messages[0]["video_id"] == str(video.id)

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(video.id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "UPLOADED"


@pytest.mark.asyncio
async def test_complete_video_is_idempotent_for_uploaded_processing_or_ready(session_factory) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id, status="PROCESSING")
    storage_client = InMemoryStorageClient()
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    await seed_video(session_factory, video)

    result = await service.complete_video(
        video.id,
        VideoCompleteRequest(),
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )

    assert result.status_code == 200
    assert result.payload.status == "PROCESSING"
    assert broker_client.published_messages == []


@pytest.mark.asyncio
async def test_complete_video_rejects_missing_blob(session_factory) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(InvalidArgumentError) as exc_info:
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert "not found in storage" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_complete_video_rejects_object_larger_than_2gb(session_factory) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    storage_client = InMemoryStorageClient()
    storage_client.put_object(video.storage_path, b"x" * 8)
    storage_client.get_blob_metadata(video.storage_path)
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=type(
            "LargeObjectStorage",
            (),
            {
                "generate_signed_url": staticmethod(lambda request: None),
                "delete_object": staticmethod(lambda object_name: True),
                "get_blob_metadata": staticmethod(
                    lambda object_name: type(
                        "BlobMetadataLike",
                        (),
                        {"exists": True, "size_bytes": MAX_UPLOAD_SIZE_BYTES + 1, "etag": "etag"},
                    )()
                ),
            },
        )(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(InvalidArgumentError) as exc_info:
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert "2gb" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_complete_video_returns_403_for_other_user(session_factory) -> None:
    owner_id = uuid4()
    other_user_id = uuid4()
    video = build_video(user_id=owner_id)
    await seed_video(session_factory, video)
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(ForbiddenError):
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=other_user_id,
            trace_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_complete_video_returns_404_for_unknown_video(session_factory) -> None:
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(NotFoundError):
        await service.complete_video(
            uuid4(),
            VideoCompleteRequest(),
            requester_user_id=uuid4(),
            trace_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_complete_video_keeps_uploaded_status_when_broker_publish_fails(session_factory) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    storage_client = InMemoryStorageClient()
    storage_client.put_object(video.storage_path, b"video-bytes")
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=InMemoryBrokerClient(failures_before_success=3),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert "publish failed after retries" in str(exc_info.value).lower()

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(video.id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "UPLOADED"
