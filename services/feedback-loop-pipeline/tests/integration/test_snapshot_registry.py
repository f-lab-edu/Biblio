from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import Base, ModelSnapshotModel
from src.infra.db.snapshot_registry import ModelSnapshotStore


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


def _ts(minute: int) -> datetime:
    return datetime(2026, 6, 1, 12, minute, tzinfo=UTC)


async def _statuses(session) -> dict[str, str]:
    rows = (await session.execute(select(ModelSnapshotModel))).scalars().all()
    return {row.model_version: row.status for row in rows}


@pytest.mark.asyncio
async def test_record_cutover_promotes_and_demotes(session):
    store = ModelSnapshotStore(session)
    await store.record_cutover(model_version="v1", index_name="index-v1", captured_at=_ts(0))
    await store.record_cutover(model_version="v2", index_name="index-v2", captured_at=_ts(1))
    await store.record_cutover(model_version="v3", index_name="index-v3", captured_at=_ts(2))
    await session.flush()
    assert await _statuses(session) == {"v1": "SUPERSEDED", "v2": "PREVIOUS_STABLE", "v3": "ACTIVE"}


@pytest.mark.asyncio
async def test_rollback_target_is_previous_stable(session):
    store = ModelSnapshotStore(session)
    await store.record_cutover(model_version="v1", index_name="index-v1", captured_at=_ts(0))
    await store.record_cutover(model_version="v2", index_name="index-v2", captured_at=_ts(1))
    await session.flush()
    target = await store.get_rollback_target()
    assert (target.model_version, target.index_name) == ("v1", "index-v1")


@pytest.mark.asyncio
async def test_record_rollback_promotes_next_superseded(session):
    store = ModelSnapshotStore(session)
    await store.record_cutover(model_version="v1", index_name="index-v1", captured_at=_ts(0))
    await store.record_cutover(model_version="v2", index_name="index-v2", captured_at=_ts(1))
    await store.record_cutover(model_version="v3", index_name="index-v3", captured_at=_ts(2))
    await session.flush()

    await store.record_rollback(restored_at=_ts(3))
    await session.flush()
    assert await _statuses(session) == {"v3": "ROLLED_BACK", "v2": "ACTIVE", "v1": "PREVIOUS_STABLE"}

    await store.record_rollback(restored_at=_ts(4))
    await session.flush()
    assert await _statuses(session) == {"v3": "ROLLED_BACK", "v2": "ROLLED_BACK", "v1": "ACTIVE"}


@pytest.mark.asyncio
async def test_get_rollback_target_none_when_no_prior(session):
    store = ModelSnapshotStore(session)
    await store.record_cutover(model_version="v1", index_name="index-v1", captured_at=_ts(0))
    await session.flush()
    assert await store.get_rollback_target() is None


@pytest.mark.asyncio
async def test_active_invariant_after_mixed_sequence(session):
    from sqlalchemy import func as safunc

    store = ModelSnapshotStore(session)
    for i, v in enumerate(["v1", "v2", "v3"]):
        await store.record_cutover(model_version=v, index_name=f"index-{v}", captured_at=_ts(i))
    await session.flush()
    await store.record_rollback(restored_at=_ts(5))
    await store.record_cutover(model_version="v4", index_name="index-v4", captured_at=_ts(6))
    await session.flush()

    active_count = await session.scalar(
        select(safunc.count()).select_from(ModelSnapshotModel)
        .where(ModelSnapshotModel.status == "ACTIVE")
    )
    assert active_count == 1
