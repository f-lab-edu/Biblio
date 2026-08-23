from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from loguru import logger
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.ai.google_stt_adapter import TranscriptWordDTO
from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineFrameCandidateModel,
    PipelineRunModel,
    TranscriptSegmentModel,
    VideoModel,
)
from src.infra.db.release_repository import EmbeddingTarget
from src.infra.storage.client import StorageClient
from src.services.chunking_service import SentenceFragment
from src.services.transcript_assembly_service import (
    AssemblyPart,
    AssemblyProgress,
    TranscriptAssemblyService,
)
from src.services.transcription_artifact import TranscriptionArtifact


class IngestTargetProvider(Protocol):
    async def get_online_ingest_target(
        self,
        *,
        fallback_model_version: str,
        fallback_index_name: str = "default-index",
    ) -> EmbeddingTarget: ...


@dataclass(frozen=True, slots=True)
class AssemblyDecision:
    advanced: bool
    reason: str
    parts_applied: int = 0
    chunks_generated: int = 0


@dataclass(slots=True)
class AssemblySnapshot:
    pipeline_run_id: UUID
    video_id: UUID
    parts: list[AssemblyPart]
    ready_parts: list[AssemblyPart]
    next_part_index: int
    next_chunk_index: int
    pending_words: list[TranscriptWordDTO]
    chunk_buffer: list[SentenceFragment]
    final_flush: bool
    duration_ms: int


class TranscriptAssemblyCoordinator:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        storage: StorageClient,
        service: TranscriptAssemblyService,
        target_provider: IngestTargetProvider,
        fallback_embedding_model_version: str,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._service = service
        self._target_provider = target_provider
        self._fallback_embedding_model_version = fallback_embedding_model_version

    async def advance(
        self,
        *,
        pipeline_run_id: UUID,
        trace_id: UUID,
    ) -> AssemblyDecision:
        started_at = perf_counter()
        work_id = uuid4()
        logger.bind(
            log_schema_version=2,
            event_name="assembly.started",
            stage="ASSEMBLE_CHUNKS",
            pipeline_run_id=str(pipeline_run_id),
            trace_id=str(trace_id),
            work_id=str(work_id),
        ).info("assembly.started")
        snapshot = await self._load_snapshot(pipeline_run_id)
        if snapshot is None:
            return self._emit_decision(
                AssemblyDecision(False, "inactive_or_no_contiguous_parts"),
                pipeline_run_id,
                trace_id,
                work_id,
                started_at,
            )
        artifacts = await self._load_artifacts(snapshot.ready_parts)
        progress = self._service.advance(
            all_parts=snapshot.parts,
            artifacts=artifacts,
            duration_ms=snapshot.duration_ms,
            next_part_index=snapshot.next_part_index,
            next_chunk_index=snapshot.next_chunk_index,
            pending_words=snapshot.pending_words,
            chunk_buffer=snapshot.chunk_buffer,
            final_flush=snapshot.final_flush,
        )
        target = await self._target_provider.get_online_ingest_target(
            fallback_model_version=self._fallback_embedding_model_version,
        )
        decision = await self._persist(snapshot, progress, target, work_id)
        return self._emit_decision(
            decision,
            pipeline_run_id,
            trace_id,
            work_id,
            started_at,
        )

    async def _load_snapshot(self, pipeline_run_id: UUID) -> AssemblySnapshot | None:
        async with self._session_factory() as session:
            run = await session.get(PipelineRunModel, pipeline_run_id)
            if run is None or not run.is_active or run.assembly_completed:
                return None
            video = await session.get(VideoModel, run.video_id)
            if video is None or video.status == "DELETING":
                return None
            models = list(
                (
                    await session.scalars(
                        select(PipelineAudioPartModel)
                        .where(PipelineAudioPartModel.pipeline_run_id == run.id)
                        .order_by(PipelineAudioPartModel.part_index)
                    )
                ).all()
            )
            parts = [self._to_part(model) for model in models]
            ready_parts = self._contiguous_ready_parts(run, parts)
            if not ready_parts:
                return None
            next_index = run.next_part_index + len(ready_parts)
            final_flush = bool(
                run.normalization_completed
                and run.total_part_count is not None
                and next_index == run.total_part_count
            )
            return AssemblySnapshot(
                pipeline_run_id=run.id,
                video_id=run.video_id,
                parts=parts,
                ready_parts=ready_parts,
                next_part_index=run.next_part_index,
                next_chunk_index=run.next_chunk_index,
                pending_words=self._load_words(run.pending_words),
                chunk_buffer=self._load_fragments(run.chunk_buffer),
                final_flush=final_flush,
                duration_ms=max(part.end_ms for part in parts),
            )

    @staticmethod
    def _contiguous_ready_parts(
        run: PipelineRunModel,
        parts: list[AssemblyPart],
    ) -> list[AssemblyPart]:
        by_index = {part.part_index: part for part in parts}
        ready: list[AssemblyPart] = []
        part_index = run.next_part_index
        while True:
            part = by_index.get(part_index)
            if part is None or part.status != "COMPLETED" or part.result_ref is None:
                break
            if by_index.get(part_index + 1) is None and not (
                run.normalization_completed
                and run.total_part_count == part_index + 1
            ):
                break
            ready.append(part)
            part_index += 1
        return ready

    async def _load_artifacts(
        self,
        parts: list[AssemblyPart],
    ) -> list[TranscriptionArtifact]:
        artifacts: list[TranscriptionArtifact] = []
        with TemporaryDirectory(prefix="biblio-assembly-") as directory:
            root = Path(directory)
            for part in parts:
                assert part.result_ref is not None
                destination = root / f"part-{part.part_index:05d}.json"
                await self._storage.download_object(part.result_ref, destination)
                artifacts.append(TranscriptionArtifact.from_bytes(destination.read_bytes()))
        return artifacts

    async def _persist(
        self,
        snapshot: AssemblySnapshot,
        progress: AssemblyProgress,
        target: EmbeddingTarget,
        work_id: UUID,
    ) -> AssemblyDecision:
        transaction_started_at = perf_counter()
        async with self._session_factory() as session:
            async with session.begin():
                video = await session.get(VideoModel, snapshot.video_id, with_for_update=True)
                run = await session.get(
                    PipelineRunModel,
                    snapshot.pipeline_run_id,
                    with_for_update=True,
                )
                if not self._can_persist(video, run, snapshot):
                    return AssemblyDecision(False, "cursor_or_run_changed")
                if not await self._parts_still_match(session, snapshot.ready_parts):
                    return AssemblyDecision(False, "part_identity_changed")
                assert run is not None
                await self._write_progress(session, run, snapshot, progress, target)
        logger.bind(
            log_schema_version=2,
            event_name="db.transaction.succeeded",
            stage="ASSEMBLE_CHUNKS",
            pipeline_run_id=str(snapshot.pipeline_run_id),
            work_id=str(work_id),
            operation="persist_assembly_progress",
            duration_ms=round((perf_counter() - transaction_started_at) * 1000),
        ).info("db.transaction.succeeded")
        return AssemblyDecision(
            True,
            "completed" if progress.completed else "advanced",
            parts_applied=len(snapshot.ready_parts),
            chunks_generated=len(progress.chunks),
        )

    async def _write_progress(
        self,
        session: AsyncSession,
        run: PipelineRunModel,
        snapshot: AssemblySnapshot,
        progress: AssemblyProgress,
        target: EmbeddingTarget,
    ) -> None:
        if snapshot.next_part_index == 0:
            await session.execute(
                delete(TranscriptSegmentModel).where(
                    TranscriptSegmentModel.video_id == snapshot.video_id
                )
            )
        segment_index = int(
            await session.scalar(
                select(func.count(TranscriptSegmentModel.id)).where(
                    TranscriptSegmentModel.video_id == snapshot.video_id
                )
            )
            or 0
        )
        for offset, segment in enumerate(progress.segments):
            session.add(
                TranscriptSegmentModel(
                    id=uuid4(),
                    video_id=snapshot.video_id,
                    segment_index=segment_index + offset,
                    text=segment.text,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    stt_model_version=snapshot.ready_parts[0].stt_model_version,
                )
            )
        await self._write_chunks(session, snapshot, progress, target)
        run.next_part_index = progress.next_part_index
        run.next_chunk_index = progress.next_chunk_index
        run.pending_words = self._dump_words(progress.pending_words)
        run.chunk_buffer = self._dump_fragments(progress.chunk_buffer)
        if progress.completed:
            run.transcript_completed = True
            run.assembly_completed = True
        await session.flush()

    async def _write_chunks(
        self,
        session: AsyncSession,
        snapshot: AssemblySnapshot,
        progress: AssemblyProgress,
        target: EmbeddingTarget,
    ) -> None:
        candidates = list(
            (
                await session.scalars(
                    select(PipelineFrameCandidateModel).where(
                        PipelineFrameCandidateModel.pipeline_run_id
                        == snapshot.pipeline_run_id
                    )
                )
            ).all()
        )
        now = await session.scalar(select(func.now()))
        for chunk in progress.chunks:
            frame_ref = self._select_frame_ref(candidates, chunk.start_ms, chunk.end_ms)
            status = "READY" if snapshot.final_flush and frame_ref else "WAITING_FRAME"
            session.add(
                PipelineChunkWorkModel(
                    chunk_work_id=uuid4(),
                    pipeline_run_id=snapshot.pipeline_run_id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    start_ms=chunk.start_ms,
                    end_ms=chunk.end_ms,
                    frame_ref=frame_ref if snapshot.final_flush else None,
                    chunking_version=chunk.chunking_version,
                    stt_model_version=snapshot.ready_parts[0].stt_model_version,
                    embedding_model_version=target.model_version,
                    index_name=target.index_name,
                    enrichment_status=status,
                    enrichment_ready_at=now if status == "READY" else None,
                )
            )
        if snapshot.final_flush:
            await self._promote_waiting_chunks(session, snapshot.pipeline_run_id, candidates, now)

    @staticmethod
    async def _promote_waiting_chunks(
        session: AsyncSession,
        pipeline_run_id: UUID,
        candidates: list[PipelineFrameCandidateModel],
        now: datetime | None,
    ) -> None:
        waiting = list(
            (
                await session.scalars(
                    select(PipelineChunkWorkModel).where(
                        PipelineChunkWorkModel.pipeline_run_id == pipeline_run_id,
                        PipelineChunkWorkModel.enrichment_status == "WAITING_FRAME",
                    )
                )
            ).all()
        )
        for work in waiting:
            frame_ref = TranscriptAssemblyCoordinator._select_frame_ref(
                candidates,
                work.start_ms,
                work.end_ms,
            )
            if frame_ref is not None:
                work.frame_ref = frame_ref
                work.enrichment_status = "READY"
                work.enrichment_ready_at = now

    @staticmethod
    def _select_frame_ref(
        candidates: list[PipelineFrameCandidateModel],
        start_ms: int,
        end_ms: int,
    ) -> str | None:
        if not candidates:
            return None
        midpoint = (start_ms + end_ms) / 2
        in_range = [
            candidate
            for candidate in candidates
            if start_ms <= candidate.timestamp_ms <= end_ms
        ]
        pool = in_range or candidates
        selected = min(
            pool,
            key=lambda candidate: (abs(candidate.timestamp_ms - midpoint), candidate.frame_index),
        )
        return selected.frame_gcs_path

    @staticmethod
    def _can_persist(
        video: VideoModel | None,
        run: PipelineRunModel | None,
        snapshot: AssemblySnapshot,
    ) -> bool:
        return bool(
            video is not None
            and video.status != "DELETING"
            and run is not None
            and run.is_active
            and not run.assembly_completed
            and run.next_part_index == snapshot.next_part_index
            and run.next_chunk_index == snapshot.next_chunk_index
        )

    @staticmethod
    async def _parts_still_match(
        session: AsyncSession,
        parts: list[AssemblyPart],
    ) -> bool:
        for expected in parts:
            current = await session.get(
                PipelineAudioPartModel,
                expected.audio_part_id,
                with_for_update=True,
            )
            if current is None or current.status != "COMPLETED":
                return False
            identity = (
                current.pipeline_run_id,
                current.part_index,
                current.start_ms,
                current.end_ms,
                current.stt_model_version,
                current.result_ref,
            )
            expected_identity = (
                expected.pipeline_run_id,
                expected.part_index,
                expected.start_ms,
                expected.end_ms,
                expected.stt_model_version,
                expected.result_ref,
            )
            if identity != expected_identity:
                return False
        return True

    @staticmethod
    def _to_part(model: PipelineAudioPartModel) -> AssemblyPart:
        return AssemblyPart(
            pipeline_run_id=model.pipeline_run_id,
            audio_part_id=model.audio_part_id,
            part_index=model.part_index,
            start_ms=model.start_ms,
            end_ms=model.end_ms,
            audio_gcs_path=model.audio_gcs_path,
            stt_model_version=model.stt_model_version,
            status=model.status,
            result_ref=model.result_ref,
        )

    @staticmethod
    def _load_words(payload: list[dict[str, object]]) -> list[TranscriptWordDTO]:
        return [
            TranscriptWordDTO(
                text=str(item["text"]),
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
            )
            for item in payload
        ]

    @staticmethod
    def _dump_words(words: list[TranscriptWordDTO]) -> list[dict[str, object]]:
        return [
            {"text": word.text, "start_ms": word.start_ms, "end_ms": word.end_ms}
            for word in words
        ]

    @staticmethod
    def _load_fragments(payload: list[dict[str, object]]) -> list[SentenceFragment]:
        return [
            SentenceFragment(
                text=str(item["text"]),
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
            )
            for item in payload
        ]

    @staticmethod
    def _dump_fragments(
        fragments: list[SentenceFragment],
    ) -> list[dict[str, object]]:
        return [
            {
                "text": fragment.text,
                "start_ms": fragment.start_ms,
                "end_ms": fragment.end_ms,
            }
            for fragment in fragments
        ]

    @staticmethod
    def _emit_decision(
        decision: AssemblyDecision,
        pipeline_run_id: UUID,
        trace_id: UUID,
        work_id: UUID,
        started_at: float,
    ) -> AssemblyDecision:
        event_name = "assembly.succeeded" if decision.advanced else "assembly.skipped"
        logger.bind(
            log_schema_version=2,
            event_name=event_name,
            stage="ASSEMBLE_CHUNKS",
            pipeline_run_id=str(pipeline_run_id),
            trace_id=str(trace_id),
            work_id=str(work_id),
            outcome=decision.reason,
            parts_applied=decision.parts_applied,
            chunks_generated=decision.chunks_generated,
            duration_ms=round((perf_counter() - started_at) * 1000),
        ).info(event_name)
        return decision
