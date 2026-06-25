from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import Base
from src.infra.db.models import MLPipelineRunModel
from src.run_control.db_slots import DbRunSlotStore


MODEL_VERSION_PREFIX = "bge-m3"

@pytest.fixture
async def session_factory(tmp_path):
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    
    try:
        yield factory
    finally:
        await engine.dispose()

@pytest.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as db_session:
        yield db_session
        

async def test_request_creates_running_run_when_no_active_run(session: AsyncSession) -> None:
    requested_at = datetime(2026, 5, 11, 10, 0, tzinfo = UTC)
    store = DbRunSlotStore(session, model_version_prefix=MODEL_VERSION_PREFIX)

    decision = await store.request_training_run(
        dataset_version = "dataset-v1",
        baseline_model_version="baseline-v1",
        requested_at = requested_at,
    )

    assert decision.created is True
    assert decision.should_execute_now is True
    assert decision.run.status == "RUNNING"
    assert decision.run.dataset_version == "dataset-v1"
    assert decision.run.baseline_model_version == "baseline-v1"
    assert decision.run.candidate_model_version == "bge-m3-20260511T190000.000KST"
    assert str(decision.run.id) not in decision.run.candidate_model_version


async def test_request_creates_pending_when_running_exists(session: AsyncSession) -> None:
    requested_at = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    store = DbRunSlotStore(session, model_version_prefix=MODEL_VERSION_PREFIX)

    running = await store.request_training_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        requested_at=requested_at,
    )
    # 운영에서 각 런은 별도 clock.now() 호출로 생성돼 생성 시각이 다르다.
    # 1ms 차이로 candidate 모델 버전이 구분되는지 검증한다.
    pending_requested_at = requested_at.replace(microsecond=1000)
    pending = await store.request_training_run(
        dataset_version="dataset-v2",
        baseline_model_version="baseline-v2",
        requested_at=pending_requested_at,
    )

    assert running.run.status == "RUNNING"
    assert pending.created is True
    assert pending.should_execute_now is False
    assert pending.run.status == "PENDING"
    assert pending.run.dataset_version == "dataset-v2"
    assert pending.run.baseline_model_version == "baseline-v2"
    assert pending.run.candidate_model_version != running.run.candidate_model_version
    assert str(pending.run.id) not in pending.run.candidate_model_version

async def test_new_pending_supersedes_existing_pending(session: AsyncSession) ->None:
    requested_at = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    store = DbRunSlotStore(session, model_version_prefix=MODEL_VERSION_PREFIX)

    await store.request_training_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        requested_at=requested_at,
    )
    old_pending = await store.request_training_run(
        dataset_version="dataset-v2",
        baseline_model_version="baseline-v2",
        requested_at=requested_at,
    )
    new_pending = await store.request_training_run(
        dataset_version="dataset-v3",
        baseline_model_version="baseline-v3",
        requested_at=requested_at,
    )

    assert old_pending.run.status == "SUPERSEDED"
    assert old_pending.run.superseded_by_run_id == new_pending.run.id
    assert new_pending.run.status == "PENDING"


async def test_duplicate_request_for_same_dataset_reuses_active_run(session: AsyncSession) -> None:
    requested_at = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    store = DbRunSlotStore(session, model_version_prefix=MODEL_VERSION_PREFIX)

    first = await store.request_training_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v1",
        requested_at=requested_at,
    )
    duplicate = await store.request_training_run(
        dataset_version="dataset-v1",
        baseline_model_version="baseline-v2",
        requested_at=requested_at,
    )

    run_count = await session.scalar(select(func.count()).select_from(MLPipelineRunModel))

    assert duplicate.created is False
    assert duplicate.should_execute_now is True
    assert duplicate.run.id == first.run.id
    assert duplicate.run.status == "RUNNING"
    assert run_count == 1


async def test_stale_empty_slot_read_recovers_from_running_unique_conflict(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_at = datetime(2026, 5, 11, 10, 0, tzinfo=UTC)
    existing_run = MLPipelineRunModel(
        status="RUNNING",
        dataset_version="dataset-v1",
        baseline_model_version="baseline-existing",
        candidate_model_version="candidate-existing",
        created_at=requested_at,
        updated_at=requested_at,
    )
    session.add(existing_run)
    await session.flush()

    store = DbRunSlotStore(session, model_version_prefix=MODEL_VERSION_PREFIX)

    async def stale_active_for_dataset(dataset_version: str):
        # Async to satisfy the DbRunSlotStore lookup method contract.
        _ = dataset_version
        return None

    async def stale_one_by_status(status: str):
        # Async to satisfy the DbRunSlotStore lookup method contract.
        _ = status
        return None

    monkeypatch.setattr(store, "_find_active_for_dataset", stale_active_for_dataset)
    monkeypatch.setattr(store, "_find_one_by_status", stale_one_by_status)

    decision = await store.request_training_run(
        dataset_version="dataset-v2",
        baseline_model_version="baseline-v2",
        requested_at=requested_at,
    )
    run_count = await session.scalar(select(func.count()).select_from(MLPipelineRunModel))

    assert decision.created is False
    assert decision.should_execute_now is True
    assert decision.run.id == existing_run.id
    assert decision.run.status == "RUNNING"
    assert run_count == 1
