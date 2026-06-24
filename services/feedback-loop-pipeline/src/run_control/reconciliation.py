from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from loguru import logger

from src.infra.db.models import MLPipelineRunModel, ModelReleaseModel
from src.observability.metrics import MetricsRecorder, NoopMetricsRecorder
from src.utils.clock import Clock, SystemClock


@dataclass(frozen=True)
class ReconciliationReport:
    stuck_run_ids: list[UUID]
    rollback_stuck: bool
    release_status: str | None


class ReconciliationService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        clock: Clock | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self._session = session
        self._clock = clock or SystemClock()
        self._metrics = metrics or NoopMetricsRecorder()

    async def inspect(
        self,
        *,
        stuck_run_timeout_sec: int,
        rollback_timeout_sec: int,
    ) -> ReconciliationReport:
        now = self._clock.now()
        stuck_run_cutoff = now - timedelta(seconds=stuck_run_timeout_sec)
        rollback_cutoff = now - timedelta(seconds=rollback_timeout_sec)
        stuck_run_ids = await self._stuck_running_run_ids(stuck_run_cutoff)
        release = await self._current_release()
        rollback_stuck = (
            release is not None
            and release.release_status == "ROLLBACK_PREPARING"
            and _lte_datetime(release.updated_at, rollback_cutoff)
        )
        report = ReconciliationReport(
            stuck_run_ids=stuck_run_ids,
            rollback_stuck=rollback_stuck,
            release_status=release.release_status if release is not None else None,
        )
        if report.stuck_run_ids:
            self._metrics.increment(
                "feedback_loop.stuck_run_detected_total",
                value=len(report.stuck_run_ids),
            )
        if report.rollback_stuck:
            self._metrics.increment("feedback_loop.stuck_rollback_detected_total")
        logger.bind(
            stuck_run_count=len(report.stuck_run_ids),
            rollback_stuck=report.rollback_stuck,
            release_status=report.release_status,
        ).info("reconciliation.inspect completed")
        return report

    async def _stuck_running_run_ids(self, cutoff) -> list[UUID]:
        result = await self._session.execute(
            select(MLPipelineRunModel.id)
            .where(
                MLPipelineRunModel.status == "RUNNING",
                MLPipelineRunModel.updated_at <= cutoff,
            )
            .order_by(MLPipelineRunModel.updated_at.asc())
        )
        return list(result.scalars().all())

    async def _current_release(self) -> ModelReleaseModel | None:
        result = await self._session.execute(
            select(ModelReleaseModel).where(ModelReleaseModel.singleton_key == 1)
        )
        return result.scalar_one_or_none()


def _lte_datetime(left: datetime, right: datetime) -> bool:
    if (left.tzinfo is None) == (right.tzinfo is None) and left <= right:
        return True
    return left.replace(tzinfo=None) <= right.replace(tzinfo=None)
