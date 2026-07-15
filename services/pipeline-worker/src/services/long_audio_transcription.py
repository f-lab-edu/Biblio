import asyncio
from pathlib import Path

from loguru import logger

from src.infra.ai.google_stt_adapter import (
    ExternalAIAdapterError,
    GoogleSTTAdapter,
    STTTranscriptionResult,
)
from src.infra.db.artifact_repository import ArtifactRepository, AssetRecord
from src.infra.db.video_repository import VideoRepository
from src.infra.media.ffmpeg_client import FFmpegClient
from src.infra.storage.client import StorageClient
from src.services.pipeline_errors import AudioPreparationError, DeleteRequested
from src.services.transcript_merge_service import AudioPart, TranscriptMergeService


STT_INPUT_AUDIO_PART = "STT_INPUT_AUDIO_PART"
CHIRP3_WORD_TIMESTAMP_LIMIT_MS = 20 * 60 * 1000


class LongAudioTranscriptionService:
    def __init__(
        self,
        *,
        artifact_repository: ArtifactRepository,
        video_repository: VideoRepository,
        storage_client: StorageClient,
        ffmpeg_client: FFmpegClient,
        stt_adapter: GoogleSTTAdapter,
        merge_service: TranscriptMergeService,
        part_duration_sec: int,
        part_overlap_sec: int,
        stt_concurrency: int,
        processing_timeout_sec: int,
    ) -> None:
        self._artifact_repository = artifact_repository
        self._video_repository = video_repository
        self._storage_client = storage_client
        self._ffmpeg_client = ffmpeg_client
        self._stt_adapter = stt_adapter
        self._merge_service = merge_service
        self._part_duration_ms = part_duration_sec * 1000
        self._part_overlap_ms = part_overlap_sec * 1000
        self._stt_concurrency = stt_concurrency
        self._processing_timeout_sec = processing_timeout_sec

    @staticmethod
    def requires_splitting(duration_ms: int) -> bool:
        return duration_ms > CHIRP3_WORD_TIMESTAMP_LIMIT_MS

    def plan_parts(self, video_id: str, duration_ms: int) -> list[AudioPart]:
        parts: list[AudioPart] = []
        boundary_ms = self._part_duration_ms
        index = 0
        while boundary_ms - self._part_duration_ms < duration_ms:
            nominal_start_ms = index * self._part_duration_ms
            start_ms = max(0, nominal_start_ms - (self._part_overlap_ms if index else 0))
            end_ms = min((index + 1) * self._part_duration_ms, duration_ms)
            parts.append(
                AudioPart(
                    index=index,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    storage_path=(
                        f"artifacts/{video_id}/stt-input/v1/"
                        f"part-{index:03d}-{start_ms}-{end_ms}.flac"
                    ),
                )
            )
            index += 1
            boundary_ms += self._part_duration_ms
        return parts

    async def transcribe(
        self,
        *,
        video_id: str,
        audio_storage_path: str,
        local_audio_path: Path | None,
        duration_ms: int,
        workdir: Path,
        trace_id: str,
    ) -> STTTranscriptionResult:
        parts = self.plan_parts(video_id, duration_ms)
        cleanup_paths = {part.storage_path for part in parts}
        primary_error: BaseException | None = None
        try:
            cleanup_paths.update(await self._tracked_part_paths(video_id))
            await self._cleanup_parts(video_id, cleanup_paths)
            await self._assert_not_deleting(video_id)
            source_path = await self._ensure_local_audio(
                audio_storage_path=audio_storage_path,
                local_audio_path=local_audio_path,
                workdir=workdir,
            )
            await self._prepare_parts(video_id, source_path, workdir, parts)
            results = await self._transcribe_parts(video_id, parts, trace_id)
            try:
                return self._merge_service.merge(
                    parts=parts,
                    results=results,
                    duration_ms=duration_ms,
                )
            except ValueError as exc:
                raise ExternalAIAdapterError(
                    code="INTERNAL_ERROR",
                    message=f"STT audio part merge failed: {exc}",
                    trace_id=trace_id,
                    provider="google-stt",
                    retryable=False,
                ) from exc
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                await self._cleanup_parts(video_id, cleanup_paths)
            except Exception as cleanup_error:
                logger.bind(trace_id=trace_id, video_id=video_id).error(
                    "STT audio part cleanup failed error={}", cleanup_error
                )
                if primary_error is None:
                    raise AudioPreparationError("STT audio part cleanup failed") from cleanup_error

    async def _tracked_part_paths(self, video_id: str) -> set[str]:
        try:
            assets = await self._artifact_repository.list_assets(
                video_id,
                asset_type=STT_INPUT_AUDIO_PART,
            )
        except Exception as exc:
            raise AudioPreparationError("STT audio part lookup failed") from exc
        return {asset.storage_path for asset in assets}

    async def _cleanup_parts(self, video_id: str, storage_paths: set[str]) -> None:
        try:
            if storage_paths:
                await self._storage_client.delete_objects(sorted(storage_paths))
            await self._artifact_repository.delete_assets_by_type(
                video_id,
                asset_type=STT_INPUT_AUDIO_PART,
            )
        except Exception as exc:
            raise AudioPreparationError("STT audio part cleanup failed") from exc

    async def _ensure_local_audio(
        self,
        *,
        audio_storage_path: str,
        local_audio_path: Path | None,
        workdir: Path,
    ) -> Path:
        if local_audio_path is not None:
            return local_audio_path
        downloaded_path = workdir / "audio.flac"
        try:
            await self._storage_client.download_object(audio_storage_path, downloaded_path)
        except Exception as exc:
            raise AudioPreparationError(f"Audio download failed: {audio_storage_path}") from exc
        return downloaded_path

    async def _prepare_parts(
        self,
        video_id: str,
        source_path: Path,
        workdir: Path,
        parts: list[AudioPart],
    ) -> None:
        for part in parts:
            local_part_path = workdir / f"stt-part-{part.index:03d}.flac"
            await self._extract_part(source_path, local_part_path, part)
            await self._register_part(video_id, part)
            await self._assert_not_deleting(video_id)
            try:
                await self._storage_client.upload_object(local_part_path, part.storage_path)
            except Exception as exc:
                raise AudioPreparationError(f"Audio part upload failed: {part.storage_path}") from exc
            await self._assert_not_deleting(video_id)

    async def _register_part(self, video_id: str, part: AudioPart) -> None:
        try:
            await self._artifact_repository.upsert_asset(
                video_id,
                AssetRecord(
                    asset_type=STT_INPUT_AUDIO_PART,
                    storage_path=part.storage_path,
                    start_ms=part.start_ms,
                    end_ms=part.end_ms,
                ),
            )
        except Exception as exc:
            raise AudioPreparationError(
                f"Audio part registration failed: {part.storage_path}"
            ) from exc

    async def _extract_part(self, source_path: Path, output_path: Path, part: AudioPart) -> None:
        try:
            await asyncio.to_thread(
                self._ffmpeg_client.extract_audio_part,
                source_path,
                output_path,
                start_ms=part.start_ms,
                end_ms=part.end_ms,
                timeout=self._processing_timeout_sec,
            )
        except Exception as exc:
            raise AudioPreparationError(
                f"Audio part extraction failed: {part.storage_path}"
            ) from exc

    async def _transcribe_parts(
        self,
        video_id: str,
        parts: list[AudioPart],
        trace_id: str,
    ) -> list[STTTranscriptionResult]:
        results: list[STTTranscriptionResult] = []
        for offset in range(0, len(parts), self._stt_concurrency):
            current_parts = parts[offset : offset + self._stt_concurrency]
            current_results = await asyncio.gather(
                *(self._transcribe_part(part, trace_id) for part in current_parts),
                return_exceptions=True,
            )
            error = self._first_error(video_id, current_parts, current_results, trace_id)
            if error is not None:
                raise error
            results.extend(
                result for result in current_results if isinstance(result, STTTranscriptionResult)
            )
        return results

    async def _transcribe_part(self, part: AudioPart, trace_id: str) -> STTTranscriptionResult:
        return await self._stt_adapter.transcribe(
            audio_uri=self._storage_client.object_uri(part.storage_path),
            trace_id=trace_id,
        )

    @staticmethod
    def _first_error(
        video_id: str,
        parts: list[AudioPart],
        results: list[STTTranscriptionResult | BaseException],
        trace_id: str,
    ) -> BaseException | None:
        for part, result in zip(parts, results, strict=True):
            if not isinstance(result, BaseException):
                continue
            if isinstance(result, ExternalAIAdapterError):
                logger.bind(trace_id=trace_id, video_id=video_id).error(
                    "STT audio part failed part_index={} start_ms={} end_ms={} attempts={} "
                    "code={} message={} retryable={}",
                    part.index,
                    part.start_ms,
                    part.end_ms,
                    result.attempt_count,
                    result.code,
                    result.message,
                    result.retryable,
                )
            return result
        return None

    async def _assert_not_deleting(self, video_id: str) -> None:
        if await self._video_repository.is_deleting(video_id):
            raise DeleteRequested(video_id)
