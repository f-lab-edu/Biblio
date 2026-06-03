# TODO: READY_FOR_RELEASE run이 이미 열린 같은 candidate release를 다시 열면 no-op이어야 한다.

# Given
# - READY_FOR_RELEASE 상태의 run이 있다.
# - ModelRelease는 이미 CANDIDATE_REINDEXING 상태다.
# - release의 candidate_model_version은 run의 candidate_model_version과 같다.
# - release의 candidate_index_name도 이미 있다.

# When
# - open_candidate_release를 다시 호출한다.
# Then
# - 결과 status는 already_open이다.
# - release_status는 CANDIDATE_REINDEXING 그대로다.
# - candidate_model_version은 바뀌지 않는다.
# - candidate_index_name은 바뀌지 않는다.
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import (
    Base, 
    ChunkModel,
    MLPipelineRunModel, 
    ModelReleaseModel,
    ProjectModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.infra.db.stores import (
    MLPipelineRunStore,
    ModelReleaseStore,
    VectorIndexProjectionReader,
)
from src.release.transition import ServingTransitionManager

class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 11, 11, 0, tzinfo=UTC)


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


async def test_duplicate_handoff_is_noop_when_same_candidate_is_already_open(session: AsyncSession) -> None:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)

    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-v1",
        candidate_model_version="model-v2",
        created_at=now,
        updated_at=now,
    )
    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        candidate_opened_at=now,
        created_at=now,
        updated_at=now,
    )

    session.add_all([run, release])
    await session.flush()

    manager = ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        vector_reader=VectorIndexProjectionReader(session),
    )

    result = await manager.open_candidate_release(
        run_id=run.id,
        trace_id=uuid4()
    )

    assert result.status == "already_open"
    assert result.run_id == run.id
    assert result.candidate_model_version == "model-v2"
    assert result.candidate_index_name == "candidate-index-model-v2"

    assert release.release_status == "CANDIDATE_REINDEXING"
    assert release.active_model_version == "model-v1"
    assert release.active_index_name == "active-index-v1"
    assert release.candidate_model_version == "model-v2"
    assert release.candidate_index_name == "candidate-index-model-v2"


async def test_candidate_release_open_returns_opened_result(session: AsyncSession) -> None:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-v1",
        candidate_model_version="model-v2",
        created_at=now,
        updated_at=now,
    )
    release = ModelReleaseModel(
        release_status="STABLE",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        created_at=now,
        updated_at=now,
    )
    session.add_all([run, release])
    await session.flush()

    result = await ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        vector_reader=VectorIndexProjectionReader(session),
        clock=_FixedClock(),
    ).open_candidate_release(run_id=run.id, trace_id=uuid4())

    assert result.status == "opened"
    assert result.run_id == run.id
    assert result.candidate_model_version == "model-v2"
    assert result.candidate_index_name == "candidate-index-model-v2"
    assert run.candidate_index_name == "candidate-index-model-v2"
    assert release.release_status == "CANDIDATE_REINDEXING"


async def test_candidate_release_open_does_not_overwrite_when_release_is_not_stable(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        candidate_opened_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add(release)
    await session.flush()

    opened = await ModelReleaseStore(session).try_open_candidate_release(
        candidate_model_version="model-v3",
        candidate_index_name="candidate-index-model-v3",
        updated_at=now,
    )

    assert opened is None
    assert release.release_status == "CANDIDATE_REINDEXING"
    assert release.candidate_model_version == "model-v2"
    assert release.candidate_index_name == "candidate-index-model-v2"


async def test_cutover_promotes_candidate_when_candidate_vector_rows_exist(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    user_id = uuid4()

    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        created_at=now,
        updated_at=now,
    )
    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        candidate_opened_at=now,
        created_at=now,
        updated_at=now,
    )
    project = ProjectModel(
        user_id=user_id,
        title="Project",
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    await session.flush()

    video = VideoModel(
        user_id=user_id,
        project_id=project.id,
        title="Video",
        updated_at=now,
    )
    session.add_all([run, release, video])
    await session.flush()

    chunk = ChunkModel(
        video_id=video.id,
        text="searchable chunk",
        embedding_model_version="model-v1",
    )
    session.add(chunk)
    await session.flush()

    active_entry = VectorIndexEntryModel(
        index_name="active-index-v1",
        chunk_id=chunk.id,
        user_id=user_id,
        project_id=project.id,
        video_id=video.id,
        embedding_model_version="model-v1",
        created_at=now,
    )
    candidate_entry = VectorIndexEntryModel(
        index_name="candidate-index-model-v2",
        chunk_id=chunk.id,
        user_id=user_id,
        project_id=project.id,
        video_id=video.id,
        embedding_model_version="model-v2",
        created_at=now,
    )
    session.add_all([active_entry, candidate_entry])
    await session.flush()

    from src.infra.db.snapshot_registry import ModelSnapshotStore

    # Seed the baseline generation as ACTIVE so cutover can demote it to PREVIOUS_STABLE
    await ModelSnapshotStore(session).record_cutover(
        model_version="model-v1",
        index_name="active-index-v1",
        captured_at=now,
    )

    manager = ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        vector_reader=VectorIndexProjectionReader(session),
        clock=_FixedClock(),
    )

    result = await manager.cutover_candidate_release(
        run_id=run.id,
        trace_id=uuid4(),
    )

    assert result.status == "cutover"
    assert result.run_id == run.id
    assert result.missing_candidate_chunk_ids == []

    assert run.cutover_time is not None
    assert release.release_status == "STABLE"
    assert release.active_model_version == "model-v2"
    assert release.active_index_name == "candidate-index-model-v2"
    assert release.previous_model_version == "model-v1"
    assert release.previous_index_name == "active-index-v1"
    previous_stable = await ModelSnapshotStore(session).get_rollback_target()
    assert previous_stable is not None
    assert previous_stable.model_version == "model-v1"
    assert previous_stable.index_name == "active-index-v1"
    assert release.candidate_model_version is None
    assert release.candidate_index_name is None
    assert release.candidate_ready_at is None
    assert run.candidate_index_name == "candidate-index-model-v2"


async def test_cutover_commits_before_reloading_search_targets(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    trace_id = uuid4()
    events: list[str] = []

    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        cutover_time=now,
        created_at=now,
        updated_at=now,
    )
    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        candidate_opened_at=now,
        created_at=now,
        updated_at=now,
    )
    session.add_all([run, release])
    await session.flush()

    reloader = _RecordingServingTargetReloader(events)
    result = await ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        vector_reader=VectorIndexProjectionReader(session),
        release_change_committer=_RecordingCommitter(events),
        serving_target_reloader=reloader,
    ).cutover_candidate_release(run_id=run.id, trace_id=trace_id)

    assert result.status == "cutover"
    assert events == ["commit", "reload"]
    assert reloader.trace_ids == [trace_id]


async def test_cutover_ignores_active_rows_before_candidate_opened_at(
    session: AsyncSession,
) -> None:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    candidate_opened_at = now - timedelta(minutes=10)
    user_id = uuid4()

    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        created_at=now,
        updated_at=now,
    )
    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        candidate_opened_at=candidate_opened_at,
        created_at=now,
        updated_at=now,
    )
    project = ProjectModel(user_id=user_id, title="Project", created_at=now, updated_at=now)
    session.add(project)
    await session.flush()

    video = VideoModel(user_id=user_id, project_id=project.id, title="Video", updated_at=now)
    session.add_all([run, release, video])
    await session.flush()

    old_chunk = ChunkModel(video_id=video.id, text="old chunk", embedding_model_version="model-v1")
    new_chunk = ChunkModel(video_id=video.id, text="new chunk", embedding_model_version="model-v1")
    session.add_all([old_chunk, new_chunk])
    await session.flush()

    session.add_all(
        [
            VectorIndexEntryModel(
                index_name="active-index-v1",
                chunk_id=old_chunk.id,
                user_id=user_id,
                project_id=project.id,
                video_id=video.id,
                embedding_model_version="model-v1",
                created_at=candidate_opened_at - timedelta(seconds=1),
            ),
            VectorIndexEntryModel(
                index_name="active-index-v1",
                chunk_id=new_chunk.id,
                user_id=user_id,
                project_id=project.id,
                video_id=video.id,
                embedding_model_version="model-v1",
                created_at=candidate_opened_at,
            ),
            VectorIndexEntryModel(
                index_name="candidate-index-model-v2",
                chunk_id=new_chunk.id,
                user_id=user_id,
                project_id=project.id,
                video_id=video.id,
                embedding_model_version="model-v2",
                created_at=candidate_opened_at,
            ),
        ]
    )
    await session.flush()

    result = await ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        vector_reader=VectorIndexProjectionReader(session),
        clock=_FixedClock(),
    ).cutover_candidate_release(run_id=run.id, trace_id=uuid4())

    assert result.status == "cutover"
    assert result.missing_candidate_chunk_ids == []


async def test_cutover_blocks_when_candidate_vector_rows_are_missing(
    session : AsyncSession,
) -> None:
    now = datetime(2026, 5, 11, 10, tzinfo=UTC)
    user_id = uuid4()

    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        created_at=now,
        updated_at=now,
    )

    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        candidate_opened_at=now,
        created_at=now,
        updated_at=now,
    )
    project = ProjectModel(
        user_id=user_id,
        title="Project",
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    await session.flush()

    video = VideoModel(
        user_id=user_id,
        project_id=project.id,
        title="Video",
        updated_at=now,
        
    )
    
    session.add_all([run, release, video])
    await session.flush()

    chunk = ChunkModel(
      video_id=video.id,
      text="searchable chunk",
      embedding_model_version="model-v1",
    )

    session.add_all([chunk])
    await session.flush()


    active_entry = VectorIndexEntryModel(
      index_name="active-index-v1",
      chunk_id=chunk.id,
      user_id=user_id,
      project_id=project.id,
      video_id=video.id,
      embedding_model_version="model-v1",
      created_at=now,
    )
    session.add(active_entry)
    await session.flush()

    manager = ServingTransitionManager(
      run_store=MLPipelineRunStore(session),
      release_store=ModelReleaseStore(session),
      vector_reader=VectorIndexProjectionReader(session),
      clock=_FixedClock(),
    )

    result = await manager.cutover_candidate_release(
      run_id=run.id,
      trace_id=uuid4(),
    )
    

    assert result.status == "blocked_missing_candidate_rows"
    assert result.run_id == run.id
    assert result.missing_candidate_chunk_ids == [chunk.id]

    assert release.release_status == "CANDIDATE_REINDEXING"
    assert release.active_model_version == "model-v1"
    assert release.active_index_name == "active-index-v1"
    assert release.candidate_model_version == "model-v2"
    assert release.candidate_index_name == "candidate-index-model-v2"


async def test_cutover_records_active_snapshot(session: AsyncSession) -> None:
    now = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    user_id = uuid4()

    run = MLPipelineRunModel(
        status="READY_FOR_RELEASE",
        dataset_version="dataset-v1",
        baseline_model_version="model-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        created_at=now,
        updated_at=now,
    )
    release = ModelReleaseModel(
        release_status="CANDIDATE_REINDEXING",
        active_model_version="model-v1",
        active_index_name="active-index-v1",
        candidate_model_version="model-v2",
        candidate_index_name="candidate-index-model-v2",
        candidate_opened_at=now,
        created_at=now,
        updated_at=now,
    )
    project = ProjectModel(
        user_id=user_id,
        title="Project",
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    await session.flush()

    video = VideoModel(
        user_id=user_id,
        project_id=project.id,
        title="Video",
        updated_at=now,
    )
    session.add_all([run, release, video])
    await session.flush()

    chunk = ChunkModel(
        video_id=video.id,
        text="searchable chunk",
        embedding_model_version="model-v1",
    )
    session.add(chunk)
    await session.flush()

    active_entry = VectorIndexEntryModel(
        index_name="active-index-v1",
        chunk_id=chunk.id,
        user_id=user_id,
        project_id=project.id,
        video_id=video.id,
        embedding_model_version="model-v1",
        created_at=now,
    )
    candidate_entry = VectorIndexEntryModel(
        index_name="candidate-index-model-v2",
        chunk_id=chunk.id,
        user_id=user_id,
        project_id=project.id,
        video_id=video.id,
        embedding_model_version="model-v2",
        created_at=now,
    )
    session.add_all([active_entry, candidate_entry])
    await session.flush()

    manager = ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        vector_reader=VectorIndexProjectionReader(session),
        clock=_FixedClock(),
    )

    result = await manager.cutover_candidate_release(
        run_id=run.id,
        trace_id=uuid4(),
    )

    assert result.status == "cutover"

    from sqlalchemy import select
    from src.infra.db.models import ModelSnapshotModel

    rows = (await session.execute(
        select(ModelSnapshotModel).where(ModelSnapshotModel.status == "ACTIVE")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].model_version == "model-v2"
    assert rows[0].index_name == "candidate-index-model-v2"
