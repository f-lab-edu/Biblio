import asyncio

import pytest

from src.services.pipeline_recovery import (
    PipelineRecoveryCoordinator,
    StageRecoveryPolicy,
)


class _Repository:
    def __init__(self) -> None:
        self.calls = []

    async def recover_stale_dispatched_work(
        self,
        stage,
        *,
        visibility_timeout_sec,
    ) -> int:
        self.calls.append((stage, visibility_timeout_sec))
        return 1


class _Scheduler:
    def __init__(self) -> None:
        self.calls = []

    async def dispatch_ready_work(self, stage, capacity, *, trace_id) -> int:
        self.calls.append((stage, capacity, trace_id))
        return 1


@pytest.mark.asyncio
async def test_recovery_resets_then_refills_each_stage() -> None:
    repository = _Repository()
    scheduler = _Scheduler()
    coordinator = PipelineRecoveryCoordinator(
        repository=repository,
        scheduler=scheduler,
        policies=(
            StageRecoveryPolicy("NORMALIZE_VIDEO", 1, 7200),
            StageRecoveryPolicy("TRANSCRIBE_PART", 8, 4200),
        ),
    )

    recovered = await coordinator.recover_once()

    assert recovered == 2
    assert repository.calls == [
        ("NORMALIZE_VIDEO", 7200),
        ("TRANSCRIBE_PART", 4200),
    ]
    assert [(stage, capacity) for stage, capacity, _ in scheduler.calls] == [
        ("NORMALIZE_VIDEO", 1),
        ("TRANSCRIBE_PART", 8),
    ]


@pytest.mark.asyncio
async def test_recovery_loop_stops_after_current_scan() -> None:
    stop_event = asyncio.Event()

    class _StoppingRepository(_Repository):
        async def recover_stale_dispatched_work(self, stage, *, visibility_timeout_sec):
            stop_event.set()
            return await super().recover_stale_dispatched_work(
                stage,
                visibility_timeout_sec=visibility_timeout_sec,
            )

    coordinator = PipelineRecoveryCoordinator(
        repository=_StoppingRepository(),
        scheduler=_Scheduler(),
        policies=(StageRecoveryPolicy("NORMALIZE_VIDEO", 1, 7200),),
    )

    await coordinator.run_forever(stop_event, interval_sec=0.01)
