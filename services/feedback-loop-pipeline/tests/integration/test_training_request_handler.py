from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.dataset.manifest import (
    DatasetManifest,
    DatasetManifestSelector,
    GENERATION_RULE_VERSION,
)
from src.infra.db.models import Base, MLPipelineRunModel, ModelReleaseModel
from src.infra.db.stores import ModelReleaseStore
from src.infra.storage.inmemory import InMemoryArtifactStore
from src.run_control.consumer import (
    MissingModelReleaseError,
    TrainingRequestHandler,
)
from src.run_control.db_slots import DbRunSlotStore


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 11, 10, 0, tzinfo=UTC)


class _RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def execute(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
    ) -> None:
        # Async to satisfy the RunExecutorPort test double contract.
        self.calls.append(
            {
                "run_id": run_id,
                "trace_id": trace_id,
            }
        )


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


def _manifest(dataset_version: str, created_at: datetime) -> DatasetManifest:
    return DatasetManifest(
        dataset_version=dataset_version,
        created_at=created_at,
        generation_rule_version=GENERATION_RULE_VERSION,
        source_window_start=datetime(2026, 5, 10, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 11, 0, 0, tzinfo=UTC),
        input_event_count=12,
        deduped_event_count=11,
        trainable_event_count=10,
        training_group_count=10,
        positive_count=10,
        negative_count=20,
        negative_source_counts={"exposed_unused": 20},
        missing_text_drop_count=0,
    )


async def _seed_current_release(session: AsyncSession) -> None:
    session.add(
        ModelReleaseModel(
            release_status="STABLE",
            active_model_version="baseline-v1",
            active_index_name="active-index-v1",
        )
    )
    await session.flush()


def _training_message(trace_id: UUID) -> dict[str, object]:
    return {
        "message_type": "TRAINING_REQUEST",
        "payload_version": "v1",
        "trace_id": trace_id,
        "attempt": 1,
        "issued_at": datetime(2026, 5, 11, 9, 55, tzinfo=UTC),
    }


async def test_training_request_creates_run_from_latest_eligible_dataset(
    session: AsyncSession,
    tmp_path,
) -> None:
    trace_id = uuid4()
    store = InMemoryArtifactStore(
        {
            "feedback/datasets/dataset-v1/manifest.json": _manifest(
                "dataset-v1",
                datetime(2026, 5, 10, 12, 0, tzinfo=UTC),
            )
            .to_json()
            .encode(),
            "feedback/datasets/dataset-v2/manifest.json": _manifest(
                "dataset-v2",
                datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
            )
            .to_json()
            .encode(),
        }
    )
    await _seed_current_release(session)
    executor = _RecordingExecutor()

    result = await TrainingRequestHandler(
        manifest_selector=DatasetManifestSelector(store),
        dataset_artifact_prefix="feedback/datasets",
        run_slot_store=DbRunSlotStore(session),
        model_release_store=ModelReleaseStore(session),
        run_executor=executor,
        workspace_dir=tmp_path,
        clock=_FixedClock(),
    ).handle(_training_message(trace_id))

    run = await session.scalar(select(MLPipelineRunModel))

    assert result.created is True
    assert result.executed is True
    assert run is not None
    assert run.status == "RUNNING"
    assert run.dataset_version == "dataset-v2"
    assert run.baseline_model_version == "baseline-v1"
    assert run.candidate_model_version is not None
    assert executor.calls == [
        {
            "run_id": run.id,
            "trace_id": trace_id,
        }
    ]


async def test_training_request_without_eligible_dataset_does_not_create_run(
    session: AsyncSession,
    tmp_path,
) -> None:
    store = InMemoryArtifactStore()
    await _seed_current_release(session)
    executor = _RecordingExecutor()

    result = await TrainingRequestHandler(
        manifest_selector=DatasetManifestSelector(store),
        dataset_artifact_prefix="feedback/datasets",
        run_slot_store=DbRunSlotStore(session),
        model_release_store=ModelReleaseStore(session),
        run_executor=executor,
        workspace_dir=tmp_path,
        clock=_FixedClock(),
    ).handle(_training_message(uuid4()))

    run_count = await session.scalar(select(func.count()).select_from(MLPipelineRunModel))

    assert result.status == "NO_ELIGIBLE_DATASET"
    assert result.created is False
    assert result.executed is False
    assert run_count == 0
    assert executor.calls == []


async def test_training_request_without_current_release_does_not_create_run(
    session: AsyncSession,
    tmp_path,
) -> None:
    store = InMemoryArtifactStore(
        {
            "feedback/datasets/dataset-v1/manifest.json": _manifest(
                "dataset-v1",
                datetime(2026, 5, 11, 12, 0, tzinfo=UTC),
            )
            .to_json()
            .encode(),
        }
    )
    executor = _RecordingExecutor()

    with pytest.raises(MissingModelReleaseError):
        await TrainingRequestHandler(
            manifest_selector=DatasetManifestSelector(store),
            dataset_artifact_prefix="feedback/datasets",
            run_slot_store=DbRunSlotStore(session),
            model_release_store=ModelReleaseStore(session),
            run_executor=executor,
            workspace_dir=tmp_path,
            clock=_FixedClock(),
        ).handle(_training_message(uuid4()))

    run_count = await session.scalar(select(func.count()).select_from(MLPipelineRunModel))

    assert run_count == 0
    assert executor.calls == []
