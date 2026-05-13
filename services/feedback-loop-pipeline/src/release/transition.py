from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from loguru import logger

from src.infra.db.stores import (
    MLPipelineRunStore,
    ModelReleaseStore,
    VectorIndexProjectionReader,
)
from src.utils.clock import Clock, SystemClock


class CandidateReadinessPort(Protocol):
    async def is_candidate_ready(self, *, model_version: str) -> bool: ...


class AlwaysReadyCandidateReadiness:
    async def is_candidate_ready(self, *, model_version: str) -> bool:
        # Async to satisfy CandidateReadinessPort for local and unit wiring.
        _ = model_version
        return True


class ReleaseTransitionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateReleaseResult:
    status: str
    run_id: UUID
    candidate_model_version: str
    candidate_index_name: str


@dataclass(frozen=True)
class CutoverResult:
    status: str
    run_id: UUID
    missing_candidate_chunk_ids: list[UUID]


def candidate_index_name_for_model(candidate_model_version: str) -> str:
    return f"candidate-index-{candidate_model_version}"


class ServingTransitionManager:
    def __init__(
        self,
        *,
        run_store: MLPipelineRunStore,
        release_store: ModelReleaseStore,
        vector_reader: VectorIndexProjectionReader,
        readiness: CandidateReadinessPort | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._run_store = run_store
        self._release_store = release_store
        self._vector_reader = vector_reader
        self._readiness = readiness or AlwaysReadyCandidateReadiness()
        self._clock = clock or SystemClock()

    async def open_candidate_release(self, *, run_id: UUID, trace_id: UUID) -> CandidateReleaseResult:
        run = await self._run_store.get(run_id)
        if run is None:
            raise ReleaseTransitionError(f"MLPipelineRun not found: {run_id}")
        if run.status != "READY_FOR_RELEASE":
            raise ReleaseTransitionError(f"MLPipelineRun is not READY_FOR_RELEASE: {run_id}")
        if run.candidate_model_version is None:
            raise ReleaseTransitionError(f"MLPipelineRun has no candidate model version: {run_id}")

        release = await self._release_store.get_current()
        if release is None:
            raise ReleaseTransitionError("current ModelRelease row is required")
        candidate_index_name = run.candidate_index_name or candidate_index_name_for_model(run.candidate_model_version)
        if (
            release.release_status == "CANDIDATE_REINDEXING"
            and release.candidate_model_version == run.candidate_model_version
            and release.candidate_index_name == candidate_index_name
        ):
            if run.candidate_index_name is None:
                now = self._clock.now()
                await self._run_store.record_candidate_index(
                    run_id=run.id,
                    candidate_index_name=candidate_index_name,
                    updated_at=now,
                )
            self._log_transition(
                trace_id=trace_id,
                run_id=run.id,
                action="candidate_release_open",
                result="already_open",
                release_status=release.release_status,
                candidate_model_version=run.candidate_model_version,
                candidate_index_name=candidate_index_name,
            )
            return CandidateReleaseResult(
                status="already_open",
                run_id=run.id,
                candidate_model_version=run.candidate_model_version,
                candidate_index_name=candidate_index_name,
            )
        if release.release_status != "STABLE":
            raise ReleaseTransitionError(f"ModelRelease is not STABLE: {release.release_status}")

        now = self._clock.now()
        opened_release = await self._release_store.try_open_candidate_release(
            candidate_model_version=run.candidate_model_version,
            candidate_index_name=candidate_index_name,
            updated_at=now,
        )
        if opened_release is None:
            current_release = await self._release_store.get_current()
            current_status = current_release.release_status if current_release is not None else "missing"
            raise ReleaseTransitionError(f"ModelRelease is not STABLE: {current_status}")
        await self._run_store.record_candidate_index(
            run_id=run.id,
            candidate_index_name=candidate_index_name,
            updated_at=now,
        )
        self._log_transition(
            trace_id=trace_id,
            run_id=run.id,
            action="candidate_release_open",
            result="opened",
            release_status=opened_release.release_status,
            candidate_model_version=run.candidate_model_version,
            candidate_index_name=candidate_index_name,
        )
        return CandidateReleaseResult(
            status="opened",
            run_id=run.id,
            candidate_model_version=run.candidate_model_version,
            candidate_index_name=candidate_index_name,
        )

    async def cutover_candidate_release(self, *, run_id: UUID, trace_id: UUID) -> CutoverResult:
        run = await self._run_store.get(run_id)
        if run is None:
            raise ReleaseTransitionError(f"MLPipelineRun not found: {run_id}")
        release = await self._release_store.get_current()
        if release is None:
            raise ReleaseTransitionError("current ModelRelease row is required")
        if release.release_status != "CANDIDATE_REINDEXING":
            raise ReleaseTransitionError(f"ModelRelease is not CANDIDATE_REINDEXING: {release.release_status}")
        if release.candidate_model_version is None or release.candidate_index_name is None:
            raise ReleaseTransitionError("candidate release fields are required for cutover")
        if release.candidate_opened_at is None:
            raise ReleaseTransitionError("candidate_opened_at is required for cutover")
        if run.candidate_model_version != release.candidate_model_version:
            raise ReleaseTransitionError("run candidate model does not match current release")

        if not await self._readiness.is_candidate_ready(model_version=release.candidate_model_version):
            self._log_transition(
                trace_id=trace_id,
                run_id=run.id,
                action="candidate_release_cutover",
                result="blocked_not_ready",
                release_status=release.release_status,
                candidate_model_version=release.candidate_model_version,
                candidate_index_name=release.candidate_index_name,
            )
            return CutoverResult(status="blocked_not_ready", run_id=run.id, missing_candidate_chunk_ids=[])

        cutover_time = run.cutover_time or self._clock.now()
        if run.cutover_time is None:
            await self._run_store.record_cutover_time(
                run_id=run.id,
                cutover_time=cutover_time,
                updated_at=cutover_time,
            )
        missing_chunk_ids = await self._vector_reader.find_missing_candidate_chunk_ids(
            active_index_name=release.active_index_name,
            active_model_version=release.active_model_version,
            candidate_index_name=release.candidate_index_name,
            candidate_model_version=release.candidate_model_version,
            candidate_opened_at=release.candidate_opened_at,
            cutover_time=cutover_time,
        )
        if missing_chunk_ids:
            self._log_transition(
                trace_id=trace_id,
                run_id=run.id,
                action="candidate_release_cutover",
                result="blocked_missing_candidate_rows",
                release_status=release.release_status,
                candidate_model_version=release.candidate_model_version,
                candidate_index_name=release.candidate_index_name,
                cutover_time=cutover_time,
                missing_candidate_chunk_count=len(missing_chunk_ids),
            )
            return CutoverResult(
                status="blocked_missing_candidate_rows",
                run_id=run.id,
                missing_candidate_chunk_ids=missing_chunk_ids,
            )

        cutover_candidate_model_version = release.candidate_model_version
        cutover_candidate_index_name = release.candidate_index_name
        await self._release_store.mark_candidate_ready(ready_at=cutover_time)
        await self._release_store.complete_candidate_cutover(switched_at=cutover_time)
        self._log_transition(
            trace_id=trace_id,
            run_id=run.id,
            action="candidate_release_cutover",
            result="cutover",
            release_status="STABLE",
            candidate_model_version=cutover_candidate_model_version,
            candidate_index_name=cutover_candidate_index_name,
            cutover_time=cutover_time,
            missing_candidate_chunk_count=0,
        )
        return CutoverResult(status="cutover", run_id=run.id, missing_candidate_chunk_ids=[])

    @staticmethod
    def _log_transition(
        *,
        trace_id: UUID,
        run_id: UUID,
        action: str,
        result: str,
        release_status: str,
        candidate_model_version: str | None,
        candidate_index_name: str | None,
        cutover_time: datetime | None = None,
        missing_candidate_chunk_count: int | None = None,
    ) -> None:
        fields = {
            "trace_id": str(trace_id),
            "run_id": str(run_id),
            "action": action,
            "result": result,
            "release_status": release_status,
            "candidate_model_version": candidate_model_version,
            "candidate_index_name": candidate_index_name,
            "cutover_time": cutover_time.isoformat() if cutover_time is not None else None,
            "missing_candidate_chunk_count": missing_candidate_chunk_count,
        }
        logger.bind(**{key: value for key, value in fields.items() if value is not None}).info(
            "release.transition handled"
        )


class CandidateReleaseHandoffSink:
    def __init__(self, manager: ServingTransitionManager) -> None:
        self._manager = manager

    async def ready_for_release(self, *, run_id: UUID, trace_id: UUID) -> None:
        await self._manager.open_candidate_release(run_id=run_id, trace_id=trace_id)
