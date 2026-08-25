import asyncio
from time import perf_counter
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from loguru import logger

from src.infra.storage.client import MediaInput
from src.schemas.messages import NormalizeVideoMessage


def _normalization_log(message: NormalizeVideoMessage):
    return logger.bind(
        log_schema_version=2,
        trace_id=str(message.trace_id),
        video_id=str(message.video_id),
        pipeline_run_id=str(message.pipeline_run_id),
        stage="NORMALIZE_VIDEO",
        work_id=str(message.pipeline_run_id),
        work_attempt=message.attempt,
        queue_name=message.message_type.value,
    )


@dataclass(frozen=True, slots=True)
class NormalizationPart:
    part_index: int
    start_ms: int
    end_ms: int
    storage_path: str


@dataclass(frozen=True, slots=True)
class FrameCandidate:
    frame_index: int
    timestamp_ms: int
    storage_path: str


@dataclass(frozen=True, slots=True)
class PersistedNormalizationPart:
    part_index: int
    start_ms: int
    end_ms: int
    storage_path: str
    stt_model_version: str
    status: str


@dataclass(frozen=True, slots=True)
class NormalizationResumeState:
    source_path: str
    source_generation: str | None
    parts: tuple[PersistedNormalizationPart, ...]
    frames: tuple[FrameCandidate, ...]


class NormalizationMedia(Protocol):
    def probe_duration_ms(self, input_file: Path | str) -> int: ...

    def extract_audio_part(
        self,
        input_file: Path | str,
        output_file: Path,
        *,
        start_ms: int,
        end_ms: int,
    ) -> None: ...

    def extract_frame_candidate(
        self,
        input_file: Path | str,
        output_file: Path,
        *,
        timestamp_ms: int,
        max_width: int,
    ) -> None: ...


class NormalizationStorage(Protocol):
    def create_media_input(
        self,
        storage_path: str,
        *,
        expires_in_seconds: int,
        expected_generation: str | None = None,
    ) -> MediaInput: ...

    async def upload_object(self, source: Path, storage_path: str) -> None: ...

    async def delete_objects(self, storage_paths: list[str]) -> None: ...


class NormalizationResultRepository(Protocol):
    async def get_resume_state(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
    ) -> NormalizationResumeState | None: ...

    async def should_discard_artifacts(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
    ) -> bool: ...

    async def bind_source_identity(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        storage_path: str,
        generation: str,
    ) -> bool: ...

    async def complete_part_and_dispatch(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        part: NormalizationPart,
        stt_model_version: str,
        trace_id: UUID,
    ) -> bool: ...

    async def save_frame_candidate(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        frame_index: int,
        timestamp_ms: int,
        frame_gcs_path: str,
    ) -> bool: ...

    async def complete_normalization(
        self,
        *,
        video_id: UUID,
        pipeline_run_id: UUID,
        total_part_count: int,
        total_frame_count: int,
    ) -> bool: ...


class NormalizationAssemblyBoundary(Protocol):
    async def advance(
        self,
        *,
        pipeline_run_id: UUID,
        trace_id: UUID,
    ) -> object: ...


class NormalizationWorkdirs(Protocol):
    def temporary(self, video_id: UUID | str) -> AbstractContextManager[Path]: ...


def plan_normalization_parts(
    *,
    video_id: UUID,
    pipeline_run_id: UUID,
    duration_ms: int,
    part_duration_ms: int,
    overlap_ms: int,
) -> list[NormalizationPart]:
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    if part_duration_ms <= 0:
        raise ValueError("part_duration_ms must be positive")
    if overlap_ms < 0 or overlap_ms >= part_duration_ms:
        raise ValueError(
            "overlap_ms must satisfy 0 <= overlap_ms < part_duration_ms"
        )

    parts: list[NormalizationPart] = []
    part_index = 0

    while part_index * part_duration_ms < duration_ms:
        nominal_start_ms = part_index * part_duration_ms # overlap을 적용하기 전 각 part의 원래 시작 지점
        start_ms = max(
            0,
            nominal_start_ms - (overlap_ms if part_index else 0),
        )
        end_ms = min(
            (part_index + 1) * part_duration_ms,
            duration_ms,
        )
        storage_path = (
            f"artifacts/{video_id}/pipeline-runs/{pipeline_run_id}/"
            f"audio-parts/part-{part_index:03d}-{start_ms}-{end_ms}.flac"
        )

        parts.append(
            NormalizationPart(
                part_index=part_index,
                start_ms=start_ms,
                end_ms=end_ms,
                storage_path=storage_path,
            )
        )
        part_index += 1

    return parts


def plan_frame_candidates(
    *,
    video_id: UUID,
    pipeline_run_id: UUID,
    duration_ms: int,
    interval_ms: int,
) -> list[FrameCandidate]:
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    if interval_ms <= 0:
        raise ValueError("interval_ms must be positive")

    first_timestamp_ms = min(duration_ms // 2, interval_ms // 2)
    frames: list[FrameCandidate] = []
    for frame_index, timestamp_ms in enumerate(
        range(first_timestamp_ms, duration_ms, interval_ms)
    ):
        frames.append(
            FrameCandidate(
                frame_index=frame_index,
                timestamp_ms=timestamp_ms,
                storage_path=(
                    f"artifacts/{video_id}/pipeline-runs/{pipeline_run_id}/"
                    f"frame-candidates/frame-{frame_index:05d}-{timestamp_ms}.jpg"
                ),
            )
        )
    return frames


class NormalizationService:
    def __init__(
        self,
        *,
        media: NormalizationMedia,
        storage: NormalizationStorage,
        repository: NormalizationResultRepository,
        workdirs: NormalizationWorkdirs,
        part_duration_ms: int,
        overlap_ms: int,
        frame_interval_ms: int,
        frame_max_width: int,
        frame_extraction_concurrency: int,
        stt_model_version: str,
        signed_url_ttl_sec: int,
        assembly_boundary: NormalizationAssemblyBoundary | None = None,
    ) -> None:
        self._media = media
        self._storage = storage
        self._repository = repository
        self._workdirs = workdirs
        self._part_duration_ms = part_duration_ms
        self._overlap_ms = overlap_ms
        self._frame_interval_ms = frame_interval_ms
        self._frame_max_width = frame_max_width
        if frame_extraction_concurrency <= 0:
            raise ValueError("frame_extraction_concurrency must be positive")
        self._frame_extraction_concurrency = frame_extraction_concurrency
        self._stt_model_version = stt_model_version
        self._signed_url_ttl_sec = signed_url_ttl_sec
        self._assembly_boundary = assembly_boundary

    async def execute(self, message: NormalizeVideoMessage) -> None:
        resume_state = await self._repository.get_resume_state(
            video_id=message.video_id,
            pipeline_run_id=message.pipeline_run_id,
        )
        if resume_state is None:
            return

        uploaded_paths: list[str] = []

        with self._workdirs.temporary(message.video_id) as workdir:
            media_input = await asyncio.to_thread(
                self._storage.create_media_input,
                resume_state.source_path,
                expires_in_seconds=self._signed_url_ttl_sec,
                expected_generation=resume_state.source_generation,
            )
            if resume_state.source_generation is None:
                generation_bound = await self._repository.bind_source_identity(
                    video_id=message.video_id,
                    pipeline_run_id=message.pipeline_run_id,
                    storage_path=resume_state.source_path,
                    generation=media_input.generation,
                )
                if not generation_bound:
                    return
            stage_log = _normalization_log(message)
            stage_log.bind(
                event_name="gcs.media_input.ready",
                source_generation=media_input.generation,
            ).info("gcs.media_input.ready")
            probe_started_at = perf_counter()
            duration_ms = await asyncio.to_thread(
                self._media.probe_duration_ms,
                media_input.url,
            )
            stage_log.bind(
                event_name="ffmpeg.operation.succeeded",
                operation="probe",
                duration_ms=round((perf_counter() - probe_started_at) * 1000),
                media_duration_ms=duration_ms,
            ).info("ffmpeg.operation.succeeded")
            if await self._discard_if_inactive(message, uploaded_paths):
                return
            parts = self._plan_parts(message, duration_ms)
            frames = self._plan_frames(message, duration_ms)
            pending_parts = self._pending_parts(parts, resume_state.parts)
            pending_frames = self._pending_frames(frames, resume_state.frames)
            if not await self._create_parts(
                message,
                media_input.url,
                pending_parts,
                workdir,
                uploaded_paths,
            ):
                return
            if not await self._create_frames(
                message,
                media_input.url,
                pending_frames,
                workdir,
                uploaded_paths,
            ):
                return
            completed = await self._repository.complete_normalization(
                video_id=message.video_id,
                pipeline_run_id=message.pipeline_run_id,
                total_part_count=len(parts),
                total_frame_count=len(frames),
            )
            if not completed:
                await self._discard_if_inactive(message, uploaded_paths)
            else:
                stage_log.bind(
                    event_name="normalization.completed",
                    part_count=len(parts),
                    frame_count=len(frames),
                ).info("normalization.completed")
                if self._assembly_boundary is not None:
                    await self._assembly_boundary.advance(
                        pipeline_run_id=message.pipeline_run_id,
                        trace_id=message.trace_id,
                    )

    def _pending_parts(
        self,
        planned_parts: list[NormalizationPart],
        persisted_parts: tuple[PersistedNormalizationPart, ...],
    ) -> list[NormalizationPart]:
        persisted_by_index = {part.part_index: part for part in persisted_parts}
        pending: list[NormalizationPart] = []
        found_pending = False
        for planned in planned_parts:
            persisted = persisted_by_index.get(planned.part_index)
            if persisted is None:
                found_pending = True
                pending.append(planned)
                continue
            if found_pending:
                raise RuntimeError(
                    "Persisted audio parts must form a contiguous prefix"
                )
            if persisted.status in {"FAILED", "CANCELLED"}:
                raise RuntimeError("Normalization cannot resume a terminal audio part")
            identity_changed = (
                persisted.start_ms != planned.start_ms
                or persisted.end_ms != planned.end_ms
                or persisted.storage_path != planned.storage_path
                or persisted.stt_model_version != self._stt_model_version
            )
            if identity_changed:
                raise RuntimeError("Audio part identity changed during retry")
        return pending

    @staticmethod
    def _pending_frames(
        planned_frames: list[FrameCandidate],
        persisted_frames: tuple[FrameCandidate, ...],
    ) -> list[FrameCandidate]:
        persisted_by_index = {
            frame.frame_index: frame for frame in persisted_frames
        }
        pending: list[FrameCandidate] = []
        found_pending = False
        for planned in planned_frames:
            persisted = persisted_by_index.get(planned.frame_index)
            if persisted is None:
                found_pending = True
                pending.append(planned)
                continue
            if found_pending:
                raise RuntimeError(
                    "Persisted frame candidates must form a contiguous prefix"
                )
            if persisted != planned:
                raise RuntimeError("Frame candidate identity changed during retry")
        return pending

    def _plan_parts(
        self,
        message: NormalizeVideoMessage,
        duration_ms: int,
    ) -> list[NormalizationPart]:
        return plan_normalization_parts(
            video_id=message.video_id,
            pipeline_run_id=message.pipeline_run_id,
            duration_ms=duration_ms,
            part_duration_ms=self._part_duration_ms,
            overlap_ms=self._overlap_ms,
        )

    def _plan_frames(
        self,
        message: NormalizeVideoMessage,
        duration_ms: int,
    ) -> list[FrameCandidate]:
        return plan_frame_candidates(
            video_id=message.video_id,
            pipeline_run_id=message.pipeline_run_id,
            duration_ms=duration_ms,
            interval_ms=self._frame_interval_ms,
        )

    async def _create_parts(
        self,
        message: NormalizeVideoMessage,
        media_url: str,
        parts: list[NormalizationPart],
        workdir: Path,
        uploaded_paths: list[str],
    ) -> bool:
        for part in parts:
            if await self._discard_if_inactive(message, uploaded_paths):
                return False
            output_file = workdir / f"part-{part.part_index:03d}.flac"
            extraction_started_at = perf_counter()
            await asyncio.to_thread(
                self._media.extract_audio_part,
                media_url,
                output_file,
                start_ms=part.start_ms,
                end_ms=part.end_ms,
            )
            extraction_duration_ms = round(
                (perf_counter() - extraction_started_at) * 1000
            )
            output_bytes = output_file.stat().st_size
            _normalization_log(message).bind(
                event_name="ffmpeg.operation.succeeded",
                operation="extract_audio_part",
                part_index=part.part_index,
                duration_ms=extraction_duration_ms,
                output_bytes=output_bytes,
            ).info("ffmpeg.operation.succeeded")
            upload_started_at = perf_counter()
            await self._storage.upload_object(output_file, part.storage_path)
            _normalization_log(message).bind(
                event_name="gcs.operation.succeeded",
                operation="upload_audio_part",
                part_index=part.part_index,
                duration_ms=round((perf_counter() - upload_started_at) * 1000),
                object_bytes=output_bytes,
            ).info("gcs.operation.succeeded")
            uploaded_paths.append(part.storage_path)
            output_file.unlink(missing_ok=True)
            persistence_started_at = perf_counter()
            if not await self._repository.complete_part_and_dispatch(
                video_id=message.video_id,
                pipeline_run_id=message.pipeline_run_id,
                part=part,
                stt_model_version=self._stt_model_version,
                trace_id=message.trace_id,
            ):
                await self._discard_if_inactive(message, uploaded_paths)
                return False
            _normalization_log(message).bind(
                event_name="db.transaction.succeeded",
                operation="persist_audio_part_and_dispatch",
                part_index=part.part_index,
                duration_ms=round((perf_counter() - persistence_started_at) * 1000),
            ).info("db.transaction.succeeded")
        return True

    async def _create_frames(
        self,
        message: NormalizeVideoMessage,
        media_url: str,
        frames: list[FrameCandidate],
        workdir: Path,
        uploaded_paths: list[str],
    ) -> bool:
        if not frames:
            return True
        if await self._discard_if_inactive(message, uploaded_paths):
            return False

        extraction_started_at = perf_counter()
        await self._extract_frame_files(
            media_url,
            frames,
            workdir,
        )
        _normalization_log(message).bind(
            event_name="ffmpeg.operation.succeeded",
            operation="extract_frame_candidates",
            duration_ms=round((perf_counter() - extraction_started_at) * 1000),
            frame_count=len(frames),
        ).info("ffmpeg.operation.succeeded")
        for output_index, frame in enumerate(frames):
            output_file = workdir / f"frame-{output_index:05d}.jpg"
            if not output_file.is_file():
                raise RuntimeError("ffmpeg did not create every frame candidate")
            output_bytes = output_file.stat().st_size
            upload_started_at = perf_counter()
            await self._storage.upload_object(output_file, frame.storage_path)
            _normalization_log(message).bind(
                event_name="gcs.operation.succeeded",
                operation="upload_frame_candidate",
                frame_index=frame.frame_index,
                duration_ms=round((perf_counter() - upload_started_at) * 1000),
                object_bytes=output_bytes,
            ).info("gcs.operation.succeeded")
            uploaded_paths.append(frame.storage_path)
            output_file.unlink(missing_ok=True)
            if not await self._repository.save_frame_candidate(
                video_id=message.video_id,
                pipeline_run_id=message.pipeline_run_id,
                frame_index=frame.frame_index,
                timestamp_ms=frame.timestamp_ms,
                frame_gcs_path=frame.storage_path,
            ):
                await self._discard_if_inactive(message, uploaded_paths)
                return False
        return True

    async def _extract_frame_files(
        self,
        media_url: str,
        frames: list[FrameCandidate],
        workdir: Path,
    ) -> None:
        indexed_frames = list(enumerate(frames))
        for batch_start in range(
            0,
            len(indexed_frames),
            self._frame_extraction_concurrency,
        ):
            batch = indexed_frames[
                batch_start : batch_start + self._frame_extraction_concurrency
            ]
            results = await asyncio.gather(
                *(self._extract_frame_file(media_url, workdir, item) for item in batch),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    async def _extract_frame_file(
        self,
        media_url: str,
        workdir: Path,
        indexed_frame: tuple[int, FrameCandidate],
    ) -> None:
        output_index, frame = indexed_frame
        await asyncio.to_thread(
            self._media.extract_frame_candidate,
            media_url,
            workdir / f"frame-{output_index:05d}.jpg",
            timestamp_ms=frame.timestamp_ms,
            max_width=self._frame_max_width,
        )

    async def _discard_if_inactive(
        self,
        message: NormalizeVideoMessage,
        uploaded_paths: list[str],
    ) -> bool:
        should_discard = await self._repository.should_discard_artifacts(
            video_id=message.video_id,
            pipeline_run_id=message.pipeline_run_id,
        )
        if should_discard:
            await self._storage.delete_objects(uploaded_paths)
        return should_discard
