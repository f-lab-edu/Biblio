from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID, uuid4

from src.release.legacy_reindex import LegacyReindexScheduler


@dataclass(frozen=True)
class _RunResult:
    status: str


class _BusyLock:
    async def try_acquire(self) -> bool:
        return False

    async def release(self) -> None:
        raise AssertionError("busy lock must not be released")


class _AvailableLock:
    def __init__(self) -> None:
        self.released = False

    async def try_acquire(self) -> bool:
        return True

    async def release(self) -> None:
        self.released = True


class _Coordinator:
    def __init__(self) -> None:
        self.trace_ids: list[UUID] = []

    async def run_once(self, *, trace_id: UUID) -> _RunResult:
        self.trace_ids.append(trace_id)
        return _RunResult(status="processed")


class _StoppingCoordinator:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self._stop_event = stop_event
        self.trace_ids: list[UUID] = []

    async def run_once(self, *, trace_id: UUID) -> _RunResult:
        self.trace_ids.append(trace_id)
        self._stop_event.set()
        return _RunResult(status="processed")


async def test_scheduler_skips_when_global_lock_is_busy() -> None:
    coordinator = _Coordinator()

    result = await LegacyReindexScheduler(
        coordinator=coordinator,
        lock=_BusyLock(),
    ).run_once(trace_id=uuid4())

    assert result.status == "lock_busy"
    assert coordinator.trace_ids == []


async def test_scheduler_runs_and_releases_global_lock() -> None:
    coordinator = _Coordinator()
    lock = _AvailableLock()
    trace_id = uuid4()

    result = await LegacyReindexScheduler(
        coordinator=coordinator,
        lock=lock,
    ).run_once(trace_id=trace_id)

    assert result.status == "processed"
    assert coordinator.trace_ids == [trace_id]
    assert lock.released is True


async def test_scheduler_loop_runs_until_stop_event_is_set() -> None:
    stop_event = asyncio.Event()
    coordinator = _StoppingCoordinator(stop_event)
    trace_id = uuid4()

    await LegacyReindexScheduler(
        coordinator=coordinator,
        lock=_AvailableLock(),
        scan_interval_sec=60,
    ).run_until_stopped(
        stop_event=stop_event,
        trace_id_factory=lambda: trace_id,
    )

    assert coordinator.trace_ids == [trace_id]
