from collections import deque
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias, cast
from uuid import UUID


DispatchableStage = Literal[
    "NORMALIZE_VIDEO",
    "TRANSCRIBE_PART",
    "ENRICH_CHUNK",
    "EMBED_BATCH",
]


@dataclass(frozen=True, slots=True)
class ReadyWorkCandidate:
    work_id: UUID
    video_id: UUID
    pipeline_run_id: UUID


@dataclass(frozen=True, slots=True)
class ReadyEmbeddingBatchCandidate:
    batch_id: UUID


DispatchCandidate: TypeAlias = ReadyWorkCandidate | ReadyEmbeddingBatchCandidate


class PipelineDispatchTransaction(Protocol):
    async def acquire_stage_lock(self, stage: DispatchableStage) -> None: ...

    async def count_in_flight(self, stage: DispatchableStage) -> int: ...

    async def load_ready_candidates(
        self,
        stage: DispatchableStage,
    ) -> Sequence[DispatchCandidate]: ...

    async def publish_and_mark_dispatched(
        self,
        stage: DispatchableStage,
        candidate: DispatchCandidate,
        trace_id: UUID,
    ) -> bool: ...


class PipelineDispatchUnitOfWork(Protocol):
    def begin(self) -> AbstractAsyncContextManager[PipelineDispatchTransaction]: ...


class PipelineWorkScheduler:
    def __init__(self, unit_of_work: PipelineDispatchUnitOfWork) -> None:
        self._unit_of_work = unit_of_work

    async def dispatch_ready_work(
        self,
        stage: DispatchableStage,
        capacity: int,
        *,
        trace_id: UUID,
    ) -> int:
        async with self._unit_of_work.begin() as transaction:
            await transaction.acquire_stage_lock(stage)

            in_flight = await transaction.count_in_flight(stage)
            remaining_capacity = max(capacity - in_flight, 0)
            if remaining_capacity == 0:
                return 0

            candidates = await transaction.load_ready_candidates(stage)
            selected = _select_candidates_for_stage(
                stage,
                candidates,
                remaining_capacity,
            )

            dispatched_count = 0
            for candidate in selected:
                was_dispatched = await transaction.publish_and_mark_dispatched(
                    stage,
                    candidate,
                    trace_id,
                )
                dispatched_count += int(was_dispatched)

            return dispatched_count


def _select_candidates_for_stage(
    stage: DispatchableStage,
    candidates: Sequence[DispatchCandidate],
    capacity: int,
) -> list[DispatchCandidate]:
    if capacity <= 0:
        return []

    if stage in {"TRANSCRIBE_PART", "ENRICH_CHUNK"}:
        video_candidates = _require_video_candidates(stage, candidates)
        return _select_fair_candidates(video_candidates, capacity)

    if stage == "NORMALIZE_VIDEO":
        return list(_require_video_candidates(stage, candidates)[:capacity])

    if stage == "EMBED_BATCH":
        return list(_require_embedding_batches(candidates)[:capacity])

    raise ValueError(f"Unsupported dispatch stage: {stage}")


def _require_video_candidates(
    stage: DispatchableStage,
    candidates: Sequence[DispatchCandidate],
) -> list[ReadyWorkCandidate]:
    if not all(isinstance(candidate, ReadyWorkCandidate) for candidate in candidates):
        raise TypeError(f"{stage} requires video work candidates")
    return cast(list[ReadyWorkCandidate], list(candidates))


def _require_embedding_batches(
    candidates: Sequence[DispatchCandidate],
) -> list[ReadyEmbeddingBatchCandidate]:
    if not all(
        isinstance(candidate, ReadyEmbeddingBatchCandidate) for candidate in candidates
    ):
        raise TypeError("EMBED_BATCH requires embedding batch candidates")
    return cast(list[ReadyEmbeddingBatchCandidate], list(candidates))


def _select_fair_candidates(
    candidates: Sequence[ReadyWorkCandidate],
    capacity: int,
) -> list[ReadyWorkCandidate]:
    if capacity <= 0:
        return []

    candidates_by_video: dict[UUID, deque[ReadyWorkCandidate]] = {}
    video_rotation: deque[UUID] = deque()

    for candidate in candidates:
        if candidate.video_id not in candidates_by_video:
            candidates_by_video[candidate.video_id] = deque()
            video_rotation.append(candidate.video_id)

        candidates_by_video[candidate.video_id].append(candidate)

    selected: list[ReadyWorkCandidate] = []

    while video_rotation and len(selected) < capacity:
        video_id = video_rotation.popleft()
        video_candidates = candidates_by_video[video_id]
        selected.append(video_candidates.popleft())

        if video_candidates:
            video_rotation.append(video_id)

    return selected
