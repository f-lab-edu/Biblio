from contextlib import asynccontextmanager
from uuid import UUID

import pytest

from src.services.pipeline_work_scheduler import (
    DispatchCandidate,
    DispatchableStage,
    PipelineWorkScheduler,
    ReadyEmbeddingBatchCandidate,
    ReadyWorkCandidate,
    _select_candidates_for_stage,
    _select_fair_candidates,
)


VIDEO_A = UUID("00000000-0000-0000-0000-00000000000a")
VIDEO_B = UUID("00000000-0000-0000-0000-00000000000b")
TRACE_ID = UUID("00000000-0000-0000-0000-00000000000c")


def _candidate(video_id: UUID, work_number: int) -> ReadyWorkCandidate:
    return ReadyWorkCandidate(
        work_id=UUID(f"00000000-0000-0000-0000-{work_number:012d}"),
        video_id=video_id,
        pipeline_run_id=video_id,
    )


def _batch(batch_number: int) -> ReadyEmbeddingBatchCandidate:
    return ReadyEmbeddingBatchCandidate(
        batch_id=UUID(f"10000000-0000-0000-0000-{batch_number:012d}")
    )


def test_selects_one_work_per_video_before_reusing_a_video() -> None:
    candidates = [
        _candidate(VIDEO_A, 1),
        _candidate(VIDEO_A, 2),
        _candidate(VIDEO_A, 3),
        _candidate(VIDEO_B, 4),
        _candidate(VIDEO_B, 5),
    ]

    selected = _select_fair_candidates(candidates, capacity=4)

    assert [(item.video_id, item.work_id) for item in selected] == [
        (VIDEO_A, _candidate(VIDEO_A, 1).work_id),
        (VIDEO_B, _candidate(VIDEO_B, 4).work_id),
        (VIDEO_A, _candidate(VIDEO_A, 2).work_id),
        (VIDEO_B, _candidate(VIDEO_B, 5).work_id),
    ]


def test_single_video_uses_all_available_capacity() -> None:
    candidates = [
        _candidate(VIDEO_A, 1),
        _candidate(VIDEO_A, 2),
        _candidate(VIDEO_A, 3),
    ]

    selected = _select_fair_candidates(candidates, capacity=2)

    assert [item.work_id for item in selected] == [
        _candidate(VIDEO_A, 1).work_id,
        _candidate(VIDEO_A, 2).work_id,
    ]


def test_non_positive_capacity_selects_nothing() -> None:
    candidates = [_candidate(VIDEO_A, 1)]

    assert _select_fair_candidates(candidates, capacity=0) == []
    assert _select_fair_candidates(candidates, capacity=-1) == []


def test_normalization_preserves_repository_order() -> None:
    candidates = [_candidate(VIDEO_B, 1), _candidate(VIDEO_A, 2)]

    selected = _select_candidates_for_stage("NORMALIZE_VIDEO", candidates, 1)

    assert selected == [candidates[0]]


def test_embedding_batch_does_not_require_a_video_id() -> None:
    candidates = [_batch(1), _batch(2)]

    selected = _select_candidates_for_stage("EMBED_BATCH", candidates, 1)

    assert selected == [candidates[0]]


class _DispatchTransaction:
    def __init__(
        self,
        *,
        in_flight: int,
        candidates: list[DispatchCandidate],
        events: list[object],
    ) -> None:
        self.in_flight = in_flight
        self.candidates = candidates
        self.events = events
        self.is_active = False

    def _ensure_active(self) -> None:
        if not self.is_active:
            raise RuntimeError("transaction is no longer active")

    async def acquire_stage_lock(self, stage: DispatchableStage) -> None:
        self._ensure_active()
        self.events.append(("lock", stage))

    async def count_in_flight(self, stage: DispatchableStage) -> int:
        self._ensure_active()
        self.events.append(("count", stage))
        return self.in_flight

    async def load_ready_candidates(
        self,
        stage: DispatchableStage,
    ) -> list[DispatchCandidate]:
        self._ensure_active()
        self.events.append(("load", stage))
        return self.candidates

    async def publish_and_mark_dispatched(
        self,
        stage: DispatchableStage,
        candidate: DispatchCandidate,
        trace_id: UUID,
    ) -> bool:
        self._ensure_active()
        candidate_id = (
            candidate.work_id
            if isinstance(candidate, ReadyWorkCandidate)
            else candidate.batch_id
        )
        self.events.append(("publish_and_mark", stage, candidate_id, trace_id))
        return True


class _DispatchUnitOfWork:
    def __init__(self, transaction: _DispatchTransaction) -> None:
        self.transaction = transaction

    @asynccontextmanager
    async def begin(self):
        self.transaction.events.append("begin")
        self.transaction.is_active = True
        try:
            yield self.transaction
        finally:
            self.transaction.is_active = False
            self.transaction.events.append("end")


@pytest.mark.asyncio
async def test_dispatches_inside_one_transaction_in_fair_order() -> None:
    candidates = [
        _candidate(VIDEO_A, 1),
        _candidate(VIDEO_A, 2),
        _candidate(VIDEO_A, 3),
        _candidate(VIDEO_B, 4),
        _candidate(VIDEO_B, 5),
    ]
    events: list[object] = []
    transaction = _DispatchTransaction(
        in_flight=1,
        candidates=candidates,
        events=events,
    )
    scheduler = PipelineWorkScheduler(_DispatchUnitOfWork(transaction))

    dispatched = await scheduler.dispatch_ready_work(
        "TRANSCRIBE_PART",
        capacity=4,
        trace_id=TRACE_ID,
    )

    assert dispatched == 3
    assert events == [
        "begin",
        ("lock", "TRANSCRIBE_PART"),
        ("count", "TRANSCRIBE_PART"),
        ("load", "TRANSCRIBE_PART"),
        (
            "publish_and_mark",
            "TRANSCRIBE_PART",
            _candidate(VIDEO_A, 1).work_id,
            TRACE_ID,
        ),
        (
            "publish_and_mark",
            "TRANSCRIBE_PART",
            _candidate(VIDEO_B, 4).work_id,
            TRACE_ID,
        ),
        (
            "publish_and_mark",
            "TRANSCRIBE_PART",
            _candidate(VIDEO_A, 2).work_id,
            TRACE_ID,
        ),
        "end",
    ]


@pytest.mark.asyncio
async def test_does_not_load_ready_work_when_stage_is_full() -> None:
    events: list[object] = []
    transaction = _DispatchTransaction(
        in_flight=2,
        candidates=[_candidate(VIDEO_A, 1)],
        events=events,
    )
    scheduler = PipelineWorkScheduler(_DispatchUnitOfWork(transaction))

    dispatched = await scheduler.dispatch_ready_work(
        "TRANSCRIBE_PART",
        capacity=2,
        trace_id=TRACE_ID,
    )

    assert dispatched == 0
    assert events == [
        "begin",
        ("lock", "TRANSCRIBE_PART"),
        ("count", "TRANSCRIBE_PART"),
        "end",
    ]
