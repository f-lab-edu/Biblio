from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import (
    Base,
    ChunkModel,
    ProjectModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.infra.db.stores import ProjectRollbackStore
from src.observability.metrics import InMemoryMetricsRecorder
from src.release.recovery import RollbackRecoveryService


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


class _ReembeddingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, UUID | str]] = []

    async def request_reembedding(
        self,
        *,
        video_id: UUID,
        target_model_version: str,
        target_index_name: str,
    ) -> None:
        # Async to satisfy the recovery sink contract.
        self.calls.append(
            {
                "video_id": video_id,
                "target_model_version": target_model_version,
                "target_index_name": target_index_name,
            }
        )


async def test_recovery_dispatches_reembedding_for_excluded_videos_missing_restored_reflection(
    session: AsyncSession,
) -> None:
    user_id = uuid4()
    project = ProjectModel(
        user_id=user_id,
        title="Recovering project",
        search_serving_state="ROLLBACK_EXCLUDED",
    )
    session.add(project)
    await session.flush()

    missing_video = VideoModel(user_id=user_id, project_id=project.id, title="Missing", status="READY")
    restored_video = VideoModel(user_id=user_id, project_id=project.id, title="Restored", status="READY")
    session.add_all([missing_video, restored_video])
    await session.flush()

    session.add(ChunkModel(video_id=missing_video.id, text="problem", embedding_model_version="model-v2"))
    restored_chunk = ChunkModel(video_id=restored_video.id, text="restored", embedding_model_version="model-v1")
    session.add(restored_chunk)
    await session.flush()
    session.add(
        VectorIndexEntryModel(
            index_name="active-index-v1",
            chunk_id=restored_chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=restored_video.id,
            embedding_model_version="model-v1",
            created_at=datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
        )
    )
    await session.flush()

    sink = _ReembeddingSink()

    metrics = InMemoryMetricsRecorder()
    result = await RollbackRecoveryService(
        project_store=ProjectRollbackStore(session),
        reembedding_sink=sink,
        metrics=metrics,
    ).dispatch_restored_reembedding(
        active_model_version="model-v1",
        active_index_name="active-index-v1",
    )

    assert result.requested_video_count == 1
    assert sink.calls == [
        {
            "video_id": missing_video.id,
            "target_model_version": "model-v1",
            "target_index_name": "active-index-v1",
        }
    ]
    assert metrics.events[0].name == "feedback_loop.rollback_reembedding_requested_total"
    assert metrics.events[0].value == 1
