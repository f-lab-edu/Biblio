from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import (
    Base,
    ChunkModel,
    ModelReleaseModel,
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


@pytest.mark.asyncio
async def test_video_restored_by_vector_entries_ignoring_chunk_model_version(
    session: AsyncSession,
) -> None:
    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="p", search_serving_state="ROLLBACK_EXCLUDED")
    session.add(project)
    await session.flush()
    video = VideoModel(user_id=user_id, project_id=project.id, title="v", status="READY")
    session.add(video)
    await session.flush()
    # chunk.embedding_model_version is "v2" (problem model) — never updated after reembedding
    chunk = ChunkModel(video_id=video.id, text="t", embedding_model_version="v2")
    session.add(chunk)
    await session.flush()
    # vector entry exists in the active index with the active model version — restoration is complete
    session.add(
        VectorIndexEntryModel(
            index_name="index-v1",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="v1",
            created_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
    )
    await session.flush()

    store = ProjectRollbackStore(session)
    pending = await store.restored_reembedding_video_ids(
        active_model_version="v1", active_index_name="index-v1"
    )
    assert video.id not in pending


# ---------------------------------------------------------------------------
# RollbackRecoveryAdapter gate tests
# ---------------------------------------------------------------------------


class _FakeSettings:
    feedback_reembedding_queue_name: str = "feedback.reembedding"


async def _seed_stable_release(session: AsyncSession, *, active_model_version: str, active_index_name: str) -> None:
    session.add(
        ModelReleaseModel(
            release_status="STABLE",
            active_model_version=active_model_version,
            active_index_name=active_index_name,
            switched_at=datetime(2026, 5, 11, 11, 0, tzinfo=UTC),
        )
    )
    await session.flush()


async def _seed_excluded_project_with_unrestored_video(
    session: AsyncSession,
    *,
    active_model_version: str,
) -> VideoModel:
    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="Excluded", search_serving_state="ROLLBACK_EXCLUDED")
    session.add(project)
    await session.flush()
    # Video that still needs reembedding (chunk uses problem model, no vector entry yet)
    video = VideoModel(user_id=user_id, project_id=project.id, title="Pending", status="READY")
    session.add(video)
    await session.flush()
    session.add(ChunkModel(video_id=video.id, text="needs reembed", embedding_model_version="model-v2"))
    await session.flush()
    return video


@pytest.mark.asyncio
async def test_adapter_does_not_enqueue_when_release_status_is_not_stable(
    session: AsyncSession,
) -> None:
    from src.bootstrap import RollbackRecoveryAdapter
    from src.runtime.queue import InMemoryBrokerClient

    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="Excluded", search_serving_state="ROLLBACK_EXCLUDED")
    session.add(project)
    await session.flush()
    # Release is in ROLLBACK_PREPARING — gate must block
    session.add(
        ModelReleaseModel(
            release_status="ROLLBACK_PREPARING",
            active_model_version="model-v1",
            active_index_name="active-index-v1",
            switched_at=datetime(2026, 5, 11, 11, 0, tzinfo=UTC),
        )
    )
    await session.flush()

    broker = InMemoryBrokerClient()
    adapter = RollbackRecoveryAdapter(session, broker, _FakeSettings())
    await adapter.scan_and_recover()

    messages = await broker.consume("feedback.reembedding", limit=10)
    assert messages == []


@pytest.mark.asyncio
async def test_adapter_does_not_enqueue_when_no_excluded_projects(
    session: AsyncSession,
) -> None:
    from src.bootstrap import RollbackRecoveryAdapter
    from src.runtime.queue import InMemoryBrokerClient

    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="Normal", search_serving_state="SERVABLE")
    session.add(project)
    await session.flush()
    await _seed_stable_release(session, active_model_version="model-v1", active_index_name="active-index-v1")

    broker = InMemoryBrokerClient()
    adapter = RollbackRecoveryAdapter(session, broker, _FakeSettings())
    await adapter.scan_and_recover()

    messages = await broker.consume("feedback.reembedding", limit=10)
    assert messages == []


@pytest.mark.asyncio
async def test_adapter_enqueues_reembedding_for_excluded_project_with_unrestored_video(
    session: AsyncSession,
) -> None:
    from src.bootstrap import RollbackRecoveryAdapter
    from src.runtime.queue import InMemoryBrokerClient

    await _seed_stable_release(session, active_model_version="model-v1", active_index_name="active-index-v1")
    video = await _seed_excluded_project_with_unrestored_video(session, active_model_version="model-v1")

    broker = InMemoryBrokerClient()
    adapter = RollbackRecoveryAdapter(session, broker, _FakeSettings())
    await adapter.scan_and_recover()

    messages = await broker.consume("feedback.reembedding", limit=10)
    assert len(messages) >= 1
    payloads = [m.payload for m in messages]
    assert any(str(video.id) in str(p) for p in payloads)


@pytest.mark.asyncio
async def test_has_rollback_excluded_projects(session: AsyncSession) -> None:
    from uuid import uuid4
    from src.infra.db.models import ProjectModel
    from src.infra.db.stores import ProjectRollbackStore

    store = ProjectRollbackStore(session)
    assert await store.has_rollback_excluded_projects() is False

    session.add(ProjectModel(user_id=uuid4(), title="p", search_serving_state="ROLLBACK_EXCLUDED"))
    await session.flush()
    assert await store.has_rollback_excluded_projects() is True


@pytest.mark.asyncio
async def test_recovery_converges_and_returns_project_to_servable(
    session: AsyncSession,
) -> None:
    """Full convergence loop: two scan_and_recover() calls bracket simulated worker completion.

    Tick 1 — unrestored video: broker receives REEMBEDDING_REQUEST; project stays ROLLBACK_EXCLUDED.
    Simulate worker: insert VectorIndexEntryModel for the chunk.
    Tick 2 — all chunks have active-index entries: project transitions to SERVABLE.
    """
    from src.bootstrap import RollbackRecoveryAdapter
    from src.runtime.queue import InMemoryBrokerClient

    # Seed: STABLE release with active model v1 / index-v1
    active_model_version = "model-v1"
    active_index_name = "active-index-v1"
    await _seed_stable_release(
        session,
        active_model_version=active_model_version,
        active_index_name=active_index_name,
    )

    # Seed: one ROLLBACK_EXCLUDED project with one READY video and one chunk (no vector entry yet)
    user_id = uuid4()
    project = ProjectModel(
        user_id=user_id,
        title="Converging project",
        search_serving_state="ROLLBACK_EXCLUDED",
    )
    session.add(project)
    await session.flush()

    video = VideoModel(user_id=user_id, project_id=project.id, title="Target video", status="READY")
    session.add(video)
    await session.flush()

    chunk = ChunkModel(video_id=video.id, text="needs reembedding", embedding_model_version="model-v2")
    session.add(chunk)
    await session.flush()

    broker = InMemoryBrokerClient()
    adapter = RollbackRecoveryAdapter(session, broker, _FakeSettings())

    # --- Tick 1: broker should receive at least one REEMBEDDING_REQUEST for the video ---
    await adapter.scan_and_recover()

    messages = await broker.consume(_FakeSettings.feedback_reembedding_queue_name, limit=10)
    assert len(messages) >= 1, "Expected at least one REEMBEDDING_REQUEST after first recovery tick"
    payloads = [m.payload for m in messages]
    assert any(
        p.get("message_type") == "REEMBEDDING_REQUEST" and str(video.id) in str(p)
        for p in payloads
    ), f"No REEMBEDDING_REQUEST for video {video.id} found in payloads: {payloads}"

    # Project must still be ROLLBACK_EXCLUDED — worker has not finished yet
    await session.refresh(project)
    assert project.search_serving_state == "ROLLBACK_EXCLUDED"

    # --- Simulate worker: insert VectorIndexEntryModel for the chunk in the active index ---
    session.add(
        VectorIndexEntryModel(
            index_name=active_index_name,
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version=active_model_version,
            created_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        )
    )
    await session.flush()

    # --- Tick 2: all chunks restored — project must flip to SERVABLE ---
    await adapter.scan_and_recover()

    await session.refresh(project)
    assert project.search_serving_state == "SERVABLE"
