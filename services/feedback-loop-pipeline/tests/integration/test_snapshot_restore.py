from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import (
    Base,
    ChunkModel,
    ProjectModel,
    VectorIndexCatalogModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.infra.db.snapshot_restore import CatalogSnapshotIndexRestore

SNAPSHOT_INDEX = "active-index-v1"
SNAPSHOT_MODEL = "model-v1"
NOW = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


async def _add_catalog(session: AsyncSession, *, deleted_at: datetime | None = None) -> None:
    session.add(
        VectorIndexCatalogModel(
            index_name=SNAPSHOT_INDEX,
            model_version=SNAPSHOT_MODEL,
            embedding_dimension=8,
            deleted_at=deleted_at,
            created_at=NOW,
        )
    )
    await session.flush()


async def _add_entry(session: AsyncSession) -> None:
    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="P")
    session.add(project)
    await session.flush()
    video = VideoModel(user_id=user_id, project_id=project.id, title="V", status="READY")
    session.add(video)
    await session.flush()
    chunk = ChunkModel(video_id=video.id, text="t", embedding_model_version=SNAPSHOT_MODEL)
    session.add(chunk)
    await session.flush()
    session.add(
        VectorIndexEntryModel(
            index_name=SNAPSHOT_INDEX,
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version=SNAPSHOT_MODEL,
            created_at=NOW,
        )
    )
    await session.flush()


async def test_restore_true_when_catalog_alive_and_entry_exists(session: AsyncSession) -> None:
    await _add_catalog(session)
    await _add_entry(session)

    restore = CatalogSnapshotIndexRestore(session)

    assert await restore.restore_snapshot(index_name=SNAPSHOT_INDEX) is True


async def test_restore_false_when_catalog_missing(session: AsyncSession) -> None:
    await _add_entry(session)

    restore = CatalogSnapshotIndexRestore(session)

    assert await restore.restore_snapshot(index_name=SNAPSHOT_INDEX) is False


async def test_restore_false_when_catalog_deleted(session: AsyncSession) -> None:
    await _add_catalog(session, deleted_at=NOW)
    await _add_entry(session)

    restore = CatalogSnapshotIndexRestore(session)

    assert await restore.restore_snapshot(index_name=SNAPSHOT_INDEX) is False


async def test_restore_false_when_no_entry(session: AsyncSession) -> None:
    await _add_catalog(session)

    restore = CatalogSnapshotIndexRestore(session)

    assert await restore.restore_snapshot(index_name=SNAPSHOT_INDEX) is False
