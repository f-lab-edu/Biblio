from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from docker.errors import DockerException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from src.infra.db.cursor import CursorDecodeError, KeysetCursor, decode_cursor, encode_cursor
from src.infra.db.video_repository import VideoRepository
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
        pytest.skip(f"Docker is required for repository integration tests: {exc}")

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
async def session(postgres_url: str, migrated_database: None) -> AsyncSession:
    engine = create_async_engine(postgres_url, future=True)

    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE video"))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as db_session:
        yield db_session

    await engine.dispose()


@pytest.mark.asyncio
async def test_alembic_creates_video_table_and_indexes(session: AsyncSession) -> None:
    result = await session.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'video'
            """
        )
    )

    index_names = {row[0] for row in result.fetchall()}

    assert "idx_video_user_created" in index_names
    assert "idx_video_user_status" in index_names


@pytest.mark.asyncio
async def test_video_repository_enforces_tenancy_and_cursor_pagination(session: AsyncSession) -> None:
    repository = VideoRepository(session)
    owner_id = uuid4()
    other_user_id = uuid4()
    base_time = datetime(2026, 3, 12, 0, 0, tzinfo=UTC)

    owner_videos = [
        Video(
            id=uuid4(),
            user_id=owner_id,
            title=f"Owner Video {index}",
            category="GENERAL",
            input_type="LOCAL_FILE",
            storage_path=f"videos/{owner_id}/{index}.mp4",
            status="PENDING",
            created_at=base_time + timedelta(minutes=index),
            updated_at=base_time + timedelta(minutes=index),
        )
        for index in range(3)
    ]
    foreign_video = Video(
        id=uuid4(),
        user_id=other_user_id,
        title="Foreign Video",
        category="IT",
        input_type="EXTERNAL_URL",
        source_url="https://example.com/video",
        status="PENDING",
        created_at=base_time + timedelta(minutes=10),
        updated_at=base_time + timedelta(minutes=10),
    )

    for video in [*owner_videos, foreign_video]:
        await repository.add(video)

    await session.commit()

    first_page = await repository.list_for_user(owner_id, limit=2)

    assert [video.title for video in first_page.items] == ["Owner Video 2", "Owner Video 1"]
    assert first_page.next_cursor is not None
    decoded_cursor = decode_cursor(first_page.next_cursor)
    assert decoded_cursor.id == owner_videos[1].id

    second_page = await repository.list_for_user(owner_id, limit=2, cursor=first_page.next_cursor)

    assert [video.title for video in second_page.items] == ["Owner Video 0"]
    assert second_page.next_cursor is None

    owner_visible_video = await repository.get_by_id_for_user(owner_videos[0].id, owner_id)
    foreign_video_lookup = await repository.get_by_id_for_user(owner_videos[0].id, other_user_id)

    assert owner_visible_video is not None
    assert foreign_video_lookup is None


def test_decode_cursor_rejects_invalid_token() -> None:
    with pytest.raises(CursorDecodeError):
        decode_cursor("not-a-valid-cursor")


def test_encode_decode_cursor_round_trip() -> None:
    cursor = KeysetCursor(
        created_at=datetime(2026, 3, 12, 0, 0, tzinfo=UTC),
        id=uuid4(),
    )

    encoded = encode_cursor(cursor)
    decoded = decode_cursor(encoded)

    assert decoded == cursor
