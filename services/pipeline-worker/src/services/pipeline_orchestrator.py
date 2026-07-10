import asyncio
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from loguru import logger

from src.infra.ai.embedding_client import EmbeddingClient
from src.infra.ai.google_stt_adapter import GoogleSTTAdapter, STTTranscriptionResult, TranscriptSegmentDTO
from src.infra.ai.vision_adapter import VisionAdapter, extract_with_fallback
from src.infra.db.artifact_repository import (
    ArtifactRepository,
    AssetRecord,
    ChunkRecord,
    DEFAULT_VECTOR_INDEX_NAME,
    TranscriptSegmentRecord,
    VectorProjectionRecord,
)
from src.infra.db.release_repository import EmbeddingTarget, OnlineIngestTargets, ReleaseContextRepository
from src.infra.db.video_repository import PipelineState, VideoRecord, VideoRepository
from src.infra.media.ffmpeg_client import FFmpegClient
from src.infra.media.youtube_downloader import DownloadError, YoutubeDownloader
from src.infra.storage.client import StorageClient
from src.services.chunking_service import ChunkingService
from src.services.text_normalizer import normalize_enriched_text
from src.utils.workdir import WorkdirManager


@dataclass(slots=True)
class AudioArtifactRef:
    local_path: Path | None
    storage_path: str
    object_uri: str


@dataclass(slots=True)
class PipelineArtifacts:
    transcript_segments: list[TranscriptSegmentRecord]
    chunks: list[ChunkRecord]
    embeddings: list[list[float]]
    vector_projections: list[VectorProjectionRecord]


class DeleteRequested(Exception):
    pass


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        video_repository: VideoRepository,
        artifact_repository: ArtifactRepository,
        storage_client: StorageClient,
        youtube_downloader: YoutubeDownloader,
        ffmpeg_client: FFmpegClient,
        stt_adapter: GoogleSTTAdapter,
        embedding_client: EmbeddingClient,
        vision_adapter: VisionAdapter,
        workdir_manager: WorkdirManager,
        chunking_service: ChunkingService,
        embedding_batch_size: int,
        stt_model_version: str,
        embedding_model_version: str,
        release_context_repository: ReleaseContextRepository | None = None,
        chunk_concurrency: int = 2,
    ) -> None:
        self._video_repository = video_repository
        self._artifact_repository = artifact_repository
        self._storage_client = storage_client
        self._youtube_downloader = youtube_downloader
        self._ffmpeg_client = ffmpeg_client
        self._stt_adapter = stt_adapter
        self._embedding_client = embedding_client
        self._vision_adapter = vision_adapter
        self._workdir_manager = workdir_manager
        self._chunking_service = chunking_service
        self._embedding_batch_size = embedding_batch_size
        self._stt_model_version = stt_model_version
        self._embedding_model_version = embedding_model_version
        self._release_context_repository = release_context_repository
        self._chunk_concurrency = chunk_concurrency

    async def run(
        self,
        *,
        video: VideoRecord,
        trace_id: str,
        state: PipelineState,
        keep_ready_status: bool,
    ) -> PipelineArtifacts:
        timings: dict[str, float] = {}
        total_started_at = perf_counter()

        def record_timing(step_name: str, started_at: float) -> None:
            timings[step_name] = perf_counter() - started_at

        with self._workdir_manager.temporary(video.id) as workdir:
            try:
                started_at = perf_counter()
                original = await self._download_source(video, workdir)
                record_timing("download", started_at)
                await self._assert_not_deleting(video.id)

                started_at = perf_counter()
                audio_ref = await self._ensure_audio(video, workdir, original, state)
                record_timing("audio", started_at)
                await self._assert_not_deleting(video.id)

                started_at = perf_counter()
                segments, stt_result = await self._ensure_transcript(
                    video, audio_ref, state, self._stt_model_version, trace_id,
                )
                record_timing("stt", started_at)
                await self._assert_not_deleting(video.id)

                if stt_result.stt_model_version != self._stt_model_version:
                    logger.warning(
                        "STT version mismatch: expected={} actual={}",
                        self._stt_model_version,
                        stt_result.stt_model_version,
                    )

                release_targets = await self._load_release_targets()

                started_at = perf_counter()
                chunks = await self._build_enriched_chunks(
                    video, workdir, original, stt_result, release_targets.active.model_version, trace_id,
                )
                record_timing("chunk_enrichment", started_at)

                started_at = perf_counter()
                vector_projections = await self._build_vector_projections(
                    chunks,
                    trace_id=trace_id,
                    release_targets=release_targets,
                )
                embeddings = vector_projections[0].embeddings
                record_timing("embedding", started_at)

                started_at = perf_counter()
                await self._persist_results(
                    video.id,
                    chunks,
                    embeddings,
                    vector_projections=vector_projections,
                    set_ready=True,
                )
                record_timing("persist", started_at)

                artifacts = PipelineArtifacts(
                    transcript_segments=segments,
                    chunks=chunks,
                    embeddings=embeddings,
                    vector_projections=vector_projections,
                )
                self._log_timings(
                    trace_id=trace_id,
                    video_id=str(video.id),
                    status="success",
                    timings=timings,
                    total_duration=perf_counter() - total_started_at,
                )
                return artifacts
            except Exception:
                self._log_timings(
                    trace_id=trace_id,
                    video_id=str(video.id),
                    status="failed",
                    timings=timings,
                    total_duration=perf_counter() - total_started_at,
                )
                raise

    # ------------------------------------------------------------------
    # Private steps
    # ------------------------------------------------------------------

    async def _download_source(self, video: VideoRecord, workdir: Path) -> Path:
        if not video.storage_path:
            raise FileNotFoundError(f"Video storage_path missing for {video.id}")
        if video.input_type == "EXTERNAL_URL":
            return await self._download_external_source(video, workdir)

        original_path = workdir / "source.bin"
        await self._storage_client.download_object(video.storage_path, original_path)
        return original_path

    async def _download_external_source(self, video: VideoRecord, workdir: Path) -> Path:
        if not video.source_url:
            raise DownloadError(f"Video source_url missing for {video.id}")
        original_path = workdir / "source.mp4"
        await self._youtube_downloader.download(video.source_url, original_path)
        await self._storage_client.upload_object(original_path, video.storage_path)
        return original_path

    async def _ensure_audio(self, video: VideoRecord, workdir: Path, original: Path, state: PipelineState) -> AudioArtifactRef:
        audio_asset = await self._artifact_repository.get_audio_asset(video.id)
        if state.has_audio_asset and audio_asset is not None:
            return AudioArtifactRef(
                local_path=None,
                storage_path=audio_asset.storage_path,
                object_uri=self._storage_client.object_uri(audio_asset.storage_path),
            )

        audio_path = workdir / "audio.flac"
        await asyncio.to_thread(self._ffmpeg_client.extract_audio, original, audio_path)
        audio_storage_path = f"artifacts/{video.id}/audio.flac"
        await self._storage_client.upload_object(audio_path, audio_storage_path)
        await self._artifact_repository.upsert_asset(
            video.id,
            AssetRecord(asset_type="AUDIO", storage_path=audio_storage_path),
        )
        return AudioArtifactRef(
            local_path=audio_path,
            storage_path=audio_storage_path,
            object_uri=self._storage_client.object_uri(audio_storage_path),
        )

    async def _ensure_transcript(
        self,
        video: VideoRecord,
        audio_ref: AudioArtifactRef,
        state: PipelineState,
        target_stt_model_version: str,
        trace_id: str,
    ) -> tuple[list[TranscriptSegmentRecord], STTTranscriptionResult]:
        if state.has_transcript:
            transcript_segments = await self._artifact_repository.load_transcripts(
                video.id,
                stt_model_version=target_stt_model_version,
            )
        else:
            transcript_segments = []

        if not transcript_segments:
            stt_result = await self._stt_adapter.transcribe(audio_uri=audio_ref.object_uri, trace_id=trace_id)
            transcript_segments = [
                TranscriptSegmentRecord(
                    segment_index=index,
                    text=segment.text,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    stt_model_version=stt_result.stt_model_version,
                )
                for index, segment in enumerate(stt_result.segments)
            ]
            await self._artifact_repository.replace_transcripts(
                video.id,
                stt_model_version=stt_result.stt_model_version,
                segments=transcript_segments,
            )
        else:
            stt_result = STTTranscriptionResult(
                segments=[
                    TranscriptSegmentDTO(
                        text=segment.text,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                    )
                    for segment in transcript_segments
                ],
                stt_model_version=transcript_segments[0].stt_model_version if transcript_segments else "v000",
            )

        return transcript_segments, stt_result

    async def _build_enriched_chunks(
        self,
        video: VideoRecord,
        workdir: Path,
        original: Path,
        stt_result: STTTranscriptionResult,
        embedding_model_version: str,
        trace_id: str,
    ) -> list[ChunkRecord]:
        chunk_drafts = self._chunking_service.chunk_segments(stt_result.segments)
        await self._assert_not_deleting(video.id)
        sem = asyncio.Semaphore(self._chunk_concurrency)

        async def process_one(draft) -> ChunkRecord:
            async with sem:
                await self._assert_not_deleting(video.id)

                keyframe_path = workdir / f"chunk-{draft.chunk_index}.jpg"
                midpoint = (draft.start_ms + draft.end_ms) / 2000
                await asyncio.to_thread(
                    self._ffmpeg_client.extract_keyframe, original, keyframe_path, offset_sec=midpoint,
                )
                keyframe_storage_path = f"artifacts/{video.id}/keyframes/{draft.chunk_index}.jpg"
                await self._storage_client.upload_object(keyframe_path, keyframe_storage_path)
                keyframe_asset_id = await self._artifact_repository.upsert_asset(
                    video.id,
                    AssetRecord(
                        asset_type="KEYFRAME",
                        storage_path=keyframe_storage_path,
                        start_ms=draft.start_ms,
                        end_ms=draft.end_ms,
                    ),
                )

                vision = await extract_with_fallback(
                    self._vision_adapter,
                    keyframe_path=str(keyframe_path),
                    trace_id=trace_id,
                )
                enriched_text = normalize_enriched_text(
                    " ".join(
                        part for part in [draft.text, vision.visual_caption, vision.ocr_text, vision.scene_tags] if part
                    )
                )
                return ChunkRecord(
                    chunk_index=draft.chunk_index,
                    text=draft.text,
                    enriched_text=enriched_text or draft.text,
                    start_ms=draft.start_ms,
                    end_ms=draft.end_ms,
                    keyframe_asset_id=keyframe_asset_id,
                    chunking_version=draft.chunking_version,
                    stt_model_version=stt_result.stt_model_version,
                    embedding_model_version=embedding_model_version,
                    visual_caption=vision.visual_caption,
                    ocr_text=vision.ocr_text,
                    scene_tags=vision.scene_tags,
                )

        results: list[ChunkRecord] = []
        for i in range(0, len(chunk_drafts), self._chunk_concurrency):
            batch = chunk_drafts[i : i + self._chunk_concurrency]
            batch_results = await asyncio.gather(*[process_one(d) for d in batch])
            results.extend(batch_results)
        return sorted(results, key=lambda c: c.chunk_index)

    async def _build_vector_projections(
        self,
        chunks: list[ChunkRecord],
        *,
        trace_id: str,
        release_targets: OnlineIngestTargets,
    ) -> list[VectorProjectionRecord]:
        projections: list[VectorProjectionRecord] = []
        for target in release_targets.all_targets:
            embeddings: list[list[float]] = []
            for offset in range(0, len(chunks), self._embedding_batch_size):
                batch = chunks[offset : offset + self._embedding_batch_size]
                batch_result = await self._embedding_client.embed_texts(
                    [chunk.enriched_text for chunk in batch],
                    trace_id=trace_id,
                    model_version=target.model_version,
                )
                embeddings.extend(batch_result.embeddings)
            projections.append(
                VectorProjectionRecord(
                    index_name=target.index_name,
                    embedding_model_version=target.model_version,
                    embeddings=embeddings,
                )
            )
        return projections

    async def _persist_results(
        self,
        video_id: str,
        chunks: list[ChunkRecord],
        embeddings: list[list[float]],
        *,
        vector_projections: list[VectorProjectionRecord] | None = None,
        set_ready: bool,
    ) -> None:
        await self._artifact_repository.persist_chunks_and_vectors(
            video_id,
            chunks=chunks,
            embeddings=embeddings,
            vector_projections=vector_projections,
            set_ready=set_ready,
        )

    async def _load_release_targets(self) -> OnlineIngestTargets:
        if self._release_context_repository is None:
            return OnlineIngestTargets(
                active=EmbeddingTarget(
                    index_name=DEFAULT_VECTOR_INDEX_NAME,
                    model_version=self._embedding_model_version,
                )
            )
        return await self._release_context_repository.get_online_ingest_targets(
            fallback_model_version=self._embedding_model_version,
        )

    async def _assert_not_deleting(self, video_id: str) -> None:
        await self._video_repository.touch_processing(video_id)
        if await self._video_repository.is_deleting(video_id):
            raise DeleteRequested(video_id)

    @staticmethod
    def _log_timings(
        *,
        trace_id: str,
        video_id: str,
        status: str,
        timings: dict[str, float],
        total_duration: float,
    ) -> None:
        logger.bind(trace_id=trace_id, video_id=video_id).info(
            "pipeline.timing status={} download_ms={:.1f} audio_ms={:.1f} stt_ms={:.1f} "
            "chunk_enrichment_ms={:.1f} embedding_ms={:.1f} persist_ms={:.1f} total_ms={:.1f}",
            status,
            timings.get("download", 0.0) * 1000,
            timings.get("audio", 0.0) * 1000,
            timings.get("stt", 0.0) * 1000,
            timings.get("chunk_enrichment", 0.0) * 1000,
            timings.get("embedding", 0.0) * 1000,
            timings.get("persist", 0.0) * 1000,
            total_duration * 1000,
        )
