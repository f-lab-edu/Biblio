from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from loguru import logger

from src.infra.db.models import MLPipelineRunModel, ModelReleaseModel
from src.infra.db.stores import (
    MLPipelineRunStore,
    ModelReleaseStore,
    VectorIndexProjectionReader,
)
from src.utils.clock import Clock, SystemClock


class CandidateReadinessPort(Protocol):
    async def is_candidate_ready(self, *, model_version: str) -> bool: ...


class LegacyReindexCutoverGate(Protocol):
    async def ensure_cutover_ready(
        self,
        *,
        active_index_name: str,
        active_model_version: str,
        candidate_index_name: str | None,
        now: datetime,
        limit: int = 100,
    ) -> object: ...


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
    legacy_reindex_video_count: int = 0


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
        legacy_reindex_gate: LegacyReindexCutoverGate | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._run_store = run_store
        self._release_store = release_store
        self._vector_reader = vector_reader
        self._readiness = readiness or AlwaysReadyCandidateReadiness()
        self._legacy_reindex_gate = legacy_reindex_gate
        self._clock = clock or SystemClock()

    async def open_candidate_release(self, *, run_id: UUID, trace_id: UUID) -> CandidateReleaseResult:
        run = await self._load_ready_for_release_run(run_id)
        release = await self._load_current_release()
        candidate_index_name = run.candidate_index_name or candidate_index_name_for_model(run.candidate_model_version)

        already_open = await self._already_open_candidate_result(
            run=run,
            release=release,
            candidate_index_name=candidate_index_name,
            trace_id=trace_id,
        )
        if already_open is not None:
            return already_open

        return await self._open_new_candidate_release(
            run=run,
            release=release,
            candidate_index_name=candidate_index_name,
            trace_id=trace_id,
        )

    async def _load_ready_for_release_run(self, run_id: UUID):
        run = await self._run_store.get(run_id)
        if run is None:
            raise ReleaseTransitionError(f"MLPipelineRun not found: {run_id}")
        if run.status != "READY_FOR_RELEASE":
            raise ReleaseTransitionError(f"MLPipelineRun is not READY_FOR_RELEASE: {run_id}")
        if run.candidate_model_version is None:
            raise ReleaseTransitionError(f"MLPipelineRun has no candidate model version: {run_id}")
        return run

    async def _load_current_release(self):
        release = await self._release_store.get_current()
        if release is None:
            raise ReleaseTransitionError("current ModelRelease row is required")
        return release

    async def _already_open_candidate_result(
        self,
        *,
        run,
        release,
        candidate_index_name: str,
        trace_id: UUID,
    ) -> CandidateReleaseResult | None:
        if not (
            release.release_status == "CANDIDATE_REINDEXING"
            and release.candidate_model_version == run.candidate_model_version
            and release.candidate_index_name == candidate_index_name
        ):
            return None

        await self._record_candidate_index_if_missing(
            run=run,
            candidate_index_name=candidate_index_name,
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

    async def _open_new_candidate_release(
        self,
        *,
        run,
        release,
        candidate_index_name: str,
        trace_id: UUID,
    ) -> CandidateReleaseResult:
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
        await self._record_candidate_index_if_missing(
            run=run,
            candidate_index_name=candidate_index_name,
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

    async def _record_candidate_index_if_missing(
        self,
        *,
        run,
        candidate_index_name: str,
        updated_at: datetime | None = None,
    ) -> None:
        if run.candidate_index_name is not None:
            return
        await self._run_store.record_candidate_index(
            run_id=run.id,
            candidate_index_name=candidate_index_name,
            updated_at=updated_at or self._clock.now(),
        )

    # Cutover flow:
    # 1. candidate release가 열린 상태이고 run과 같은 candidate model인지 확인한다.
    # 2. candidate model serving이 준비되지 않았으면 cutover를 막는다.
    # 3. legacy reindex가 남아 있으면 cutover를 막는다.
    # 4. cutover_time을 고정한 뒤, candidate open 이후 생긴 active row의 candidate row 누락을 확인한다.
    # 5. 모든 조건이 통과하면 candidate model/index를 active로 승격한다.
    async def cutover_candidate_release(self, *, run_id: UUID, trace_id: UUID) -> CutoverResult:
        run, release = await self._load_cutover_run_and_release(run_id)

        not_ready = await self._blocked_not_ready_result(
            run=run,
            release=release,
            trace_id=trace_id,
        )
        if not_ready is not None:
            return not_ready

        cutover_time = run.cutover_time or self._clock.now()
        legacy_block = await self._blocked_legacy_reindex_result(
            run=run,
            release=release,
            cutover_time=cutover_time,
            trace_id=trace_id,
        )
        if legacy_block is not None:
            return legacy_block

        await self._record_cutover_time_if_missing(run=run, cutover_time=cutover_time)

        missing_rows = await self._blocked_missing_candidate_rows_result(
            run=run,
            release=release,
            cutover_time=cutover_time,
            trace_id=trace_id,
        )
        if missing_rows is not None:
            return missing_rows

        return await self._complete_candidate_cutover(
            run=run,
            release=release,
            cutover_time=cutover_time,
            trace_id=trace_id,
        )


    async def _load_cutover_run_and_release(self, run_id: UUID) -> tuple[MLPipelineRunModel, ModelReleaseModel]:
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
        return run, release

    async def _blocked_not_ready_result(
        self,
        *,
        run: MLPipelineRunModel,
        release: ModelReleaseModel,
        trace_id: UUID,
    ) -> CutoverResult | None:
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
        return None

    async def _blocked_legacy_reindex_result(
        self,
        *,
        run: MLPipelineRunModel,
        release: ModelReleaseModel,
        cutover_time: datetime,
        trace_id: UUID,
    ) -> CutoverResult | None:
        legacy_gate_result = await self._ensure_legacy_reindex_ready(
            active_index_name=release.active_index_name,
            active_model_version=release.active_model_version,
            candidate_index_name=release.candidate_index_name,
            cutover_time=cutover_time,
        )
        if legacy_gate_result is None or not getattr(legacy_gate_result, "blocked", False):
            return None

        remaining_count = int(getattr(legacy_gate_result, "remaining_video_count", 0))
        self._log_transition(
            trace_id=trace_id,
            run_id=run.id,
            action="candidate_release_cutover",
            result="blocked_legacy_vectors_remain",
            release_status=release.release_status,
            candidate_model_version=release.candidate_model_version,
            candidate_index_name=release.candidate_index_name,
            cutover_time=cutover_time,
            missing_candidate_chunk_count=0,
            legacy_reindex_video_count=remaining_count,
        )
        return CutoverResult(
            status="blocked_legacy_vectors_remain",
            run_id=run.id,
            missing_candidate_chunk_ids=[],
            legacy_reindex_video_count=remaining_count,
        )

    async def _record_cutover_time_if_missing(
        self,
        *,
        run: MLPipelineRunModel,
        cutover_time: datetime,
    ) -> None:
        if run.cutover_time is None:
            await self._run_store.record_cutover_time(
                run_id=run.id,
                cutover_time=cutover_time,
                updated_at=cutover_time,
            )

    async def _blocked_missing_candidate_rows_result(
        self,
        *,
        run: MLPipelineRunModel,
        release: ModelReleaseModel,
        cutover_time: datetime,
        trace_id: UUID,
    ) -> CutoverResult | None:
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
        return None

    async def _complete_candidate_cutover(
        self,
        *,
        run: MLPipelineRunModel,
        release: ModelReleaseModel,
        cutover_time: datetime,
        trace_id: UUID,
    ) -> CutoverResult:
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

    async def _ensure_legacy_reindex_ready(
        self,
        *,
        active_index_name: str,
        active_model_version: str,
        candidate_index_name: str | None,
        cutover_time: datetime,
    ) -> object | None:
        if self._legacy_reindex_gate is None:
            return None
        return await self._legacy_reindex_gate.ensure_cutover_ready(
            active_index_name=active_index_name,
            active_model_version=active_model_version,
            candidate_index_name=candidate_index_name,
            now=cutover_time,
        )

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
        legacy_reindex_video_count: int | None = None,
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
            "legacy_reindex_video_count": legacy_reindex_video_count,
        }
        logger.bind(**{key: value for key, value in fields.items() if value is not None}).info(
            "release.transition handled"
        )


class CandidateReleaseHandoffSink:
    def __init__(self, manager: ServingTransitionManager) -> None:
        self._manager = manager

    async def ready_for_release(self, *, run_id: UUID, trace_id: UUID) -> None:
        await self._manager.open_candidate_release(run_id=run_id, trace_id=trace_id)
