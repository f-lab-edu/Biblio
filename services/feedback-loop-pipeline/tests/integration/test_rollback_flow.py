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
from src.infra.db.stores import ModelReleaseStore, ProjectRollbackStore
from src.observability.metrics import InMemoryMetricsRecorder
from src.release.rollback import (
    AlwaysReadyRollbackTarget,
    ImmediateIndexRestore,
    RollbackRequestMessage,
    RollbackTransitionManager,
)


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 11, 12, 0, tzinfo=UTC)


class _RecordingReadiness:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def is_ready(self, *, model_version: str) -> bool:
        # Async to satisfy the rollback readiness port contract.
        self.calls.append(model_version)
        return True


class _RecordingRestore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def restore_snapshot(self, *, index_name: str) -> bool:
        # Async to satisfy the snapshot restore port contract.
        self.calls.append(index_name)
        return True


class _RecordingCommitter:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def commit(self) -> None:
        self._events.append("commit")


class _RecordingServingTargetReloader:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.trace_ids: list[UUID] = []

    async def reload(self, *, trace_id: UUID) -> None:
        self._events.append("reload")
        self.trace_ids.append(trace_id)


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


async def test_rollback_request_excludes_affected_projects_and_restores_snapshot(
    session: AsyncSession,
) -> None:
    switched_at = datetime(2026, 5, 11, 11, 0, tzinfo=UTC)
    user_id = uuid4()
    affected_project = ProjectModel(user_id=user_id, title="Affected")
    clean_project = ProjectModel(user_id=user_id, title="Clean")
    session.add_all([affected_project, clean_project])
    await session.flush()

    affected_video = VideoModel(user_id=user_id, project_id=affected_project.id, title="Affected video")
    clean_video = VideoModel(user_id=user_id, project_id=clean_project.id, title="Clean video")
    session.add_all([affected_video, clean_video])
    await session.flush()

    session.add_all(
        [
            ChunkModel(video_id=affected_video.id, text="bad model chunk", embedding_model_version="model-v2"),
            ChunkModel(video_id=clean_video.id, text="old model chunk", embedding_model_version="model-v1"),
            ModelReleaseModel(
                release_status="STABLE",
                active_model_version="model-v2",
                active_index_name="active-index-v2",
                previous_model_version="model-v1",
                previous_index_name="active-index-v1",
                rollback_snapshot_active_model_version="model-v1",
                rollback_snapshot_active_index_name="active-index-v1",
                rollback_snapshot_captured_at=switched_at,
                switched_at=switched_at,
            ),
        ]
    )
    await session.flush()

    metrics = InMemoryMetricsRecorder()
    manager = RollbackTransitionManager(
        release_store=ModelReleaseStore(session),
        project_store=ProjectRollbackStore(session),
        target_readiness=AlwaysReadyRollbackTarget(),
        index_restore=ImmediateIndexRestore(),
        clock=_FixedClock(),
        metrics=metrics,
    )

    result = await manager.handle_request(
        RollbackRequestMessage(
            message_type="ROLLBACK_REQUEST",
            payload_version="v1",
            trace_id=uuid4(),
            attempt=1,
            issued_at=switched_at,
            expected_active_model_version="model-v2",
            expected_switched_at=switched_at,
        )
    )

    assert result.status == "restored"
    assert result.affected_project_count == 1
    assert [event.name for event in metrics.events] == [
        "feedback_loop.rollback_request_total",
        "feedback_loop.rollback_affected_project_total",
    ]
    assert metrics.events[0].tags == {"status": "restored"}
    assert metrics.events[1].value == 1
    assert affected_project.search_serving_state == "ROLLBACK_EXCLUDED"
    assert clean_project.search_serving_state == "SERVABLE"

    release = await ModelReleaseStore(session).get_current()
    assert release is not None
    assert release.release_status == "STABLE"
    assert release.active_model_version == "model-v1"
    assert release.active_index_name == "active-index-v1"
    assert release.previous_model_version is None
    assert release.previous_index_name is None
    assert release.candidate_model_version is None
    assert release.candidate_index_name is None
    assert release.candidate_ready_at is None


async def test_rollback_commits_before_reloading_search_targets(
    session: AsyncSession,
) -> None:
    switched_at = datetime(2026, 5, 11, 11, 0, tzinfo=UTC)
    trace_id = uuid4()
    events: list[str] = []
    session.add(
        ModelReleaseModel(
            release_status="STABLE",
            active_model_version="model-v2",
            active_index_name="active-index-v2",
            previous_model_version="model-v1",
            previous_index_name="active-index-v1",
            rollback_snapshot_active_model_version="model-v1",
            rollback_snapshot_active_index_name="active-index-v1",
            rollback_snapshot_captured_at=switched_at,
            switched_at=switched_at,
        )
    )
    await session.flush()

    reloader = _RecordingServingTargetReloader(events)
    result = await RollbackTransitionManager(
        release_store=ModelReleaseStore(session),
        project_store=ProjectRollbackStore(session),
        target_readiness=AlwaysReadyRollbackTarget(),
        index_restore=ImmediateIndexRestore(),
        clock=_FixedClock(),
        release_change_committer=_RecordingCommitter(events),
        serving_target_reloader=reloader,
    ).handle_request(
        RollbackRequestMessage(
            message_type="ROLLBACK_REQUEST",
            payload_version="v1",
            trace_id=trace_id,
            attempt=1,
            issued_at=switched_at,
            expected_active_model_version="model-v2",
            expected_switched_at=switched_at,
        )
    )

    assert result.status == "restored"
    assert events == ["commit", "reload"]
    assert reloader.trace_ids == [trace_id]


async def test_stale_rollback_request_leaves_release_and_projects_unchanged(
    session: AsyncSession,
) -> None:
    switched_at = datetime(2026, 5, 11, 11, 0, tzinfo=UTC)
    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="Project")
    session.add(project)
    await session.flush()

    video = VideoModel(user_id=user_id, project_id=project.id, title="Video")
    session.add(video)
    await session.flush()

    session.add_all(
        [
            ChunkModel(video_id=video.id, text="bad model chunk", embedding_model_version="model-v2"),
            ModelReleaseModel(
                release_status="STABLE",
                active_model_version="model-v2",
                active_index_name="active-index-v2",
                rollback_snapshot_active_model_version="model-v1",
                rollback_snapshot_active_index_name="active-index-v1",
                rollback_snapshot_captured_at=switched_at,
                switched_at=switched_at,
            ),
        ]
    )
    await session.flush()

    result = await RollbackTransitionManager(
        release_store=ModelReleaseStore(session),
        project_store=ProjectRollbackStore(session),
        target_readiness=AlwaysReadyRollbackTarget(),
        index_restore=ImmediateIndexRestore(),
        clock=_FixedClock(),
    ).handle_request(
        RollbackRequestMessage(
            message_type="ROLLBACK_REQUEST",
            payload_version="v1",
            trace_id=uuid4(),
            attempt=1,
            issued_at=switched_at,
            expected_active_model_version="model-v3",
            expected_switched_at=switched_at,
        )
    )

    assert result.status == "stale_request"
    assert project.search_serving_state == "SERVABLE"
    release = await ModelReleaseStore(session).get_current()
    assert release is not None
    assert release.release_status == "STABLE"
    assert release.active_model_version == "model-v2"


async def test_rollback_preparing_redelivery_continues_restore(
    session: AsyncSession,
) -> None:
    switched_at = datetime(2026, 5, 11, 11, 0, tzinfo=UTC)
    session.add(
        ModelReleaseModel(
            release_status="ROLLBACK_PREPARING",
            active_model_version="model-v2",
            active_index_name="active-index-v2",
            previous_model_version="model-v1",
            previous_index_name="active-index-v1",
            rollback_snapshot_active_model_version="model-v1",
            rollback_snapshot_active_index_name="active-index-v1",
            rollback_snapshot_captured_at=switched_at,
            switched_at=switched_at,
        )
    )
    await session.flush()
    readiness = _RecordingReadiness()
    restore = _RecordingRestore()

    result = await RollbackTransitionManager(
        release_store=ModelReleaseStore(session),
        project_store=ProjectRollbackStore(session),
        target_readiness=readiness,
        index_restore=restore,
        clock=_FixedClock(),
    ).handle_request(
        RollbackRequestMessage(
            message_type="ROLLBACK_REQUEST",
            payload_version="v1",
            trace_id=uuid4(),
            attempt=2,
            issued_at=switched_at,
            expected_active_model_version="model-v2",
            expected_switched_at=switched_at,
        )
    )

    release = await ModelReleaseStore(session).get_current()
    assert release is not None
    assert result.status == "restored"
    assert readiness.calls == ["model-v1"]
    assert restore.calls == ["active-index-v1"]
    assert release.release_status == "STABLE"
    assert release.active_model_version == "model-v1"
    assert release.active_index_name == "active-index-v1"


async def test_reentry_keeps_project_excluded_until_restored_vector_reflection_is_complete(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    user_id = uuid4()
    project = ProjectModel(
        user_id=user_id,
        title="Recovering project",
        search_serving_state="ROLLBACK_EXCLUDED",
    )
    session.add(project)
    await session.flush()

    video = VideoModel(user_id=user_id, project_id=project.id, title="Video", status="READY")
    session.add(video)
    await session.flush()

    chunk = ChunkModel(video_id=video.id, text="restored chunk", embedding_model_version="model-v1")
    session.add(chunk)
    await session.flush()

    reopened_count = await ProjectRollbackStore(session).reenter_restored_projects(
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        updated_at=now,
    )

    assert reopened_count == 0
    assert project.search_serving_state == "ROLLBACK_EXCLUDED"

    session.add(
        VectorIndexEntryModel(
            index_name="active-index-v1",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="model-v1",
            created_at=now,
        )
    )
    await session.flush()

    reopened_count = await ProjectRollbackStore(session).reenter_restored_projects(
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        updated_at=now,
    )

    assert reopened_count == 1
    assert project.search_serving_state == "SERVABLE"


async def test_reentry_requires_all_ready_video_chunks_to_use_restored_model(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 11, 12, 0, tzinfo=UTC)
    user_id = uuid4()
    project = ProjectModel(
        user_id=user_id,
        title="Recovering project",
        search_serving_state="ROLLBACK_EXCLUDED",
    )
    session.add(project)
    await session.flush()

    video = VideoModel(user_id=user_id, project_id=project.id, title="Video", status="READY")
    session.add(video)
    await session.flush()

    session.add(ChunkModel(video_id=video.id, text="problem chunk", embedding_model_version="model-v2"))
    restored_chunk = ChunkModel(video_id=video.id, text="restored chunk", embedding_model_version="model-v1")
    session.add(restored_chunk)
    await session.flush()

    session.add(
        VectorIndexEntryModel(
            index_name="active-index-v1",
            chunk_id=restored_chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="model-v1",
            created_at=now,
        )
    )
    await session.flush()

    reopened_count = await ProjectRollbackStore(session).reenter_restored_projects(
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        updated_at=now,
    )

    assert reopened_count == 0
    assert project.search_serving_state == "ROLLBACK_EXCLUDED"
