import asyncio
from dataclasses import dataclass
from uuid import uuid4

from loguru import logger

from src.infra.db.pipeline_work_repository import PipelineWorkRepository
from src.services.pipeline_work_scheduler import DispatchableStage, PipelineWorkScheduler


@dataclass(frozen=True, slots=True)
class StageRecoveryPolicy:
    stage: DispatchableStage
    capacity: int
    visibility_timeout_sec: int


class PipelineRecoveryCoordinator:
    def __init__(
        self,
        *,
        repository: PipelineWorkRepository,
        scheduler: PipelineWorkScheduler,
        policies: tuple[StageRecoveryPolicy, ...],
    ) -> None:
        self._repository = repository
        self._scheduler = scheduler
        self._policies = policies

    async def recover_once(self) -> int:
        recovered_total = 0
        trace_id = uuid4()
        for policy in self._policies:
            recovered = await self._repository.recover_stale_dispatched_work(
                policy.stage,
                visibility_timeout_sec=policy.visibility_timeout_sec,
            )
            if recovered:
                logger.bind(
                    trace_id=str(trace_id),
                    video_id="-",
                    stage=policy.stage,
                    recovered_count=recovered,
                ).warning(
                    "pipeline.work.recovered stage={} recovered_count={}",
                    policy.stage,
                    recovered,
                )
            recovered_total += recovered
            await self._scheduler.dispatch_ready_work(
                policy.stage,
                policy.capacity,
                trace_id=trace_id,
            )
        return recovered_total

    async def run_forever(
        self,
        stop_event: asyncio.Event,
        *,
        interval_sec: float,
    ) -> None:
        while not stop_event.is_set():
            try:
                await self.recover_once()
            except Exception:
                logger.exception("pipeline recovery scan failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
            except TimeoutError:
                continue
