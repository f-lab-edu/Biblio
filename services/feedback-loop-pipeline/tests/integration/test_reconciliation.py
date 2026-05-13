from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.models import Base, MLPipelineRunModel, ModelReleaseModel
from src.observability.metrics import InMemoryMetricsRecorder
from src.run_control.reconciliation import ReconciliationService


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 11, 12, 0, tzinfo=UTC)


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


async def test_reconciliation_reports_stuck_running_runs_and_rollback(
    session: AsyncSession,
) -> None:
    now = _FixedClock().now()
    stale_run_id = uuid4()
    fresh_run_id = uuid4()
    session.add_all(
        [
            MLPipelineRunModel(
                id=stale_run_id,
                status="RUNNING",
                dataset_version="dataset-old",
                baseline_model_version="baseline-old",
                candidate_model_version="candidate-old",
                created_at=now - timedelta(hours=2),
                updated_at=now - timedelta(hours=2),
            ),
            MLPipelineRunModel(
                id=fresh_run_id,
                status="PENDING",
                dataset_version="dataset-new",
                baseline_model_version="baseline-new",
                candidate_model_version="candidate-new",
                created_at=now,
                updated_at=now,
            ),
            ModelReleaseModel(
                release_status="ROLLBACK_PREPARING",
                active_model_version="model-v2",
                active_index_name="active-index-v2",
                updated_at=now - timedelta(hours=2),
            ),
        ]
    )
    await session.flush()

    metrics = InMemoryMetricsRecorder()
    report = await ReconciliationService(
        session=session,
        clock=_FixedClock(),
        metrics=metrics,
    ).inspect(
        stuck_run_timeout_sec=3600,
        rollback_timeout_sec=300,
    )

    assert report.stuck_run_ids == [stale_run_id]
    assert report.rollback_stuck is True
    assert report.release_status == "ROLLBACK_PREPARING"
    assert [event.name for event in metrics.events] == [
        "feedback_loop.stuck_run_detected_total",
        "feedback_loop.stuck_rollback_detected_total",
    ]
    assert metrics.events[0].value == 1
