import asyncio
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Self


class EmbeddingWorkload(str, Enum):
    SEARCH = "search"
    VIDEO_PREPROCESS = "video_preprocess"
    LEGACY = "legacy"


class _QueueEntryState(str, Enum):
    WAITING = "waiting"
    GRANTED = "granted"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class AdmissionSnapshot:
    running_count: int
    search_queue_depth: int
    video_preprocess_queue_depth: int
    admitted_search_count: int
    admitted_video_preprocess_count: int


@dataclass(slots=True)
class _QueueEntry:
    workload: EmbeddingWorkload
    future: asyncio.Future[None]
    enqueued_at: float
    state: _QueueEntryState = _QueueEntryState.WAITING


class RequestLease:
    """Own one admitted request until its slot work and response complete."""

    def __init__(
        self,
        controller: "AdmissionController",
        workload: EmbeddingWorkload,
    ) -> None:
        self._controller = controller
        self._workload = workload
        self._released = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.release()

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._controller._release_request(self._workload)


class SlotLease:
    """Own one inference slot and expose how long the request waited for it."""

    def __init__(
        self,
        controller: "AdmissionController",
        queue_wait_ms: float,
    ) -> None:
        self._controller = controller
        self.queue_wait_ms = queue_wait_ms
        self._released = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.release()

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._controller._release_slot()


class AdmissionController:
    """Bound queued requests and grant the shared slot with search priority."""

    def __init__(
        self,
        max_concurrency: int,
        *,
        search_request_limit: int,
        video_preprocess_request_limit: int,
        search_wait_timeout_sec: float,
        video_preprocess_wait_timeout_sec: float,
    ) -> None:
        self._max_concurrency = max_concurrency
        self._request_limits = {
            EmbeddingWorkload.SEARCH: search_request_limit,
            EmbeddingWorkload.VIDEO_PREPROCESS: video_preprocess_request_limit,
        }
        self._wait_timeouts = {
            EmbeddingWorkload.SEARCH: search_wait_timeout_sec,
            EmbeddingWorkload.VIDEO_PREPROCESS: video_preprocess_wait_timeout_sec,
        }
        self._queues = {
            EmbeddingWorkload.SEARCH: deque[_QueueEntry](),
            EmbeddingWorkload.VIDEO_PREPROCESS: deque[_QueueEntry](),
        }
        self._admitted_counts = {
            EmbeddingWorkload.SEARCH: 0,
            EmbeddingWorkload.VIDEO_PREPROCESS: 0,
        }
        self._running_count = 0
        self._lock = asyncio.Lock()

    async def try_acquire_request(
        self,
        workload: EmbeddingWorkload,
    ) -> RequestLease | None:
        async with self._lock:
            if self._admitted_counts[workload] >= self._request_limits[workload]:
                return None
            self._admitted_counts[workload] += 1
        return RequestLease(self, workload)

    async def acquire_slot(self, workload: EmbeddingWorkload) -> SlotLease:
        started_at = time.monotonic()
        async with self._lock:
            if self._can_grant_immediately():
                self._running_count += 1
                return SlotLease(self, queue_wait_ms=0.0)
            entry = self._enqueue_locked(workload, started_at)
            self._grant_waiters_locked()

        try:
            async with asyncio.timeout(self._wait_timeouts[workload]):
                await asyncio.shield(entry.future)
        except (TimeoutError, asyncio.CancelledError):
            if await self._withdraw_entry(entry):
                await self._release_slot()
            raise

        return SlotLease(
            self,
            queue_wait_ms=(time.monotonic() - entry.enqueued_at) * 1000,
        )

    async def try_acquire_legacy(self) -> SlotLease | None:
        async with self._lock:
            if not self._can_grant_immediately():
                return None
            self._running_count += 1
        return SlotLease(self, queue_wait_ms=0.0)

    async def snapshot(self) -> AdmissionSnapshot:
        async with self._lock:
            return AdmissionSnapshot(
                running_count=self._running_count,
                search_queue_depth=len(self._queues[EmbeddingWorkload.SEARCH]),
                video_preprocess_queue_depth=len(
                    self._queues[EmbeddingWorkload.VIDEO_PREPROCESS]
                ),
                admitted_search_count=self._admitted_counts[EmbeddingWorkload.SEARCH],
                admitted_video_preprocess_count=self._admitted_counts[
                    EmbeddingWorkload.VIDEO_PREPROCESS
                ],
            )

    def _can_grant_immediately(self) -> bool:
        return self._running_count < self._max_concurrency and not self._has_waiters()

    def _has_waiters(self) -> bool:
        return any(self._queues[workload] for workload in self._queues)

    def _enqueue_locked(
        self,
        workload: EmbeddingWorkload,
        enqueued_at: float,
    ) -> _QueueEntry:
        entry = _QueueEntry(
            workload=workload,
            future=asyncio.get_running_loop().create_future(),
            enqueued_at=enqueued_at,
        )
        self._queues[workload].append(entry)
        return entry

    async def _withdraw_entry(self, entry: _QueueEntry) -> bool:
        async with self._lock:
            if entry.state is _QueueEntryState.WAITING:
                entry.state = _QueueEntryState.CANCELLED
                self._queues[entry.workload].remove(entry)
                entry.future.cancel()
                return False
            return entry.state is _QueueEntryState.GRANTED

    async def _release_request(self, workload: EmbeddingWorkload) -> None:
        async with self._lock:
            self._admitted_counts[workload] -= 1

    async def _release_slot(self) -> None:
        async with self._lock:
            if self._running_count <= 0:
                raise RuntimeError("Inference slot was released more than once.")
            self._running_count -= 1
            self._grant_waiters_locked()

    def _grant_waiters_locked(self) -> None:
        while self._running_count < self._max_concurrency:
            entry = self._next_waiter_locked()
            if entry is None:
                return
            entry.state = _QueueEntryState.GRANTED
            self._running_count += 1
            entry.future.set_result(None)

    def _next_waiter_locked(self) -> _QueueEntry | None:
        for workload in (
            EmbeddingWorkload.SEARCH,
            EmbeddingWorkload.VIDEO_PREPROCESS,
        ):
            queue = self._queues[workload]
            while queue:
                entry = queue.popleft()
                if entry.state is _QueueEntryState.WAITING:
                    return entry
        return None
