import asyncio
from dataclasses import dataclass
from pathlib import Path

from adapters.ai.embedding_client import EmbeddingClient
from adapters.ai.google_stt_adapter import GoogleSTTAdapter, STTTranscriptionResult, TranscriptSegmentDTO
from adapters.ai.vision_adapter import VisionAdapter, extract_with_fallback
from adapters.db.artifact_repository import ArtifactRepository, AssetRecord, ChunkRecord, TranscriptSegmentRecord
from adapters.db.video_repository import PipelineState, VideoRecord, VideoRepository
from adapters.media.ffmpeg_client import FFmpegClient
from adapters.storage.client import StorageClient
from services.chunking_service import ChunkingService
from services.text_normalizer import normalize_enriched_text
from utils.workdir import WorkdirManager

_CHUNK_CONCURRENCY = 5


@dataclass(slots=True)
class PipelineArtifacts:
    transcript_segments: list[TranscriptSegmentRecord]
    chunks: list[ChunkRecord]
    embeddings: list[list[float]]


class DeleteRequested(Exception):
    pass


class PipelineOrchestrator:
    def __init__(
        self,
        *,
        video_repository: VideoRepository,
        artifact_repository: ArtifactRepository,
        storage_client: StorageClient,
        ffmpeg_client: FFmpegClient,
        stt_adapter: GoogleSTTAdapter,
        embedding_client: EmbeddingClient,
        vision_adapter: VisionAdapter,
        workdir_manager: WorkdirManager,
        chunking_service: ChunkingService,
        embedding_batch_size: int,
    ) -> None:
        self._video_repository = video_repository
        self._artifact_repository = artifact_repository
        self._storage_client = storage_client
        self._ffmpeg_client = ffmpeg_client
        self._stt_adapter = stt_adapter
        self._embedding_client = embedding_client
        self._vision_adapter = vision_adapter
        self._workdir_manager = workdir_manager
        self._chunking_service = chunking_service
        self._embedding_batch_size = embedding_batch_size

    async def run(
        self,
        *,
        video: VideoRecord,
        trace_id: str,
        state: PipelineState,
        target_stt_model_version: str,
        keep_ready_status: bool,
    ) -> PipelineArtifacts:
        embedding_model_version = await self._embedding_client.get_model_version(trace_id)
        with self._workdir_manager.temporary(video.id) as workdir:
            original = await self._download_source(video, workdir)
            await self._assert_not_deleting(video.id)

            audio = await self._ensure_audio(video, workdir, original, state)
            await self._assert_not_deleting(video.id)

            segments, stt_result = await self._ensure_transcript(
                video, audio, state, target_stt_model_version, trace_id,
            )
            await self._assert_not_deleting(video.id)

            chunks = await self._build_enriched_chunks(
                video, workdir, original, stt_result, embedding_model_version, trace_id,
            )
            await self._assert_not_deleting(video.id)

            embeddings = await self._batch_embed(chunks, trace_id)
            self._persist_results(video.id, chunks, embeddings, set_ready=keep_ready_status)

            return PipelineArtifacts(
                transcript_segments=segments,
                chunks=chunks,
                embeddings=embeddings,
            )

    # ------------------------------------------------------------------
    # Private steps
    # ------------------------------------------------------------------

    async def _download_source(self, video: VideoRecord, workdir: Path) -> Path:
        original_path = workdir / "source.bin"
        if not video.storage_path:
            raise FileNotFoundError(f"Video storage_path missing for {video.id}")
        await self._storage_client.download_object(video.storage_path, original_path)
        return original_path

    async def _ensure_audio(self, video: VideoRecord, workdir: Path, original: Path, state: PipelineState) -> Path:
        audio_path = workdir / "audio.flac"
        audio_asset = self._artifact_repository.get_audio_asset(video.id)
        if state.has_audio_asset and audio_asset is not None:
            await self._storage_client.download_object(audio_asset.storage_path, audio_path)
        else:
            await asyncio.to_thread(self._ffmpeg_client.extract_audio, original, audio_path)
            audio_storage_path = f"artifacts/{video.id}/audio.flac"
            await self._storage_client.upload_object(audio_path, audio_storage_path)
            self._artifact_repository.upsert_asset(
                video.id,
                AssetRecord(asset_type="AUDIO", storage_path=audio_storage_path),
            )
        return audio_path

    async def _ensure_transcript(
        self,
        video: VideoRecord,
        audio: Path,
        state: PipelineState,
        target_stt_model_version: str,
        trace_id: str,
    ) -> tuple[list[TranscriptSegmentRecord], STTTranscriptionResult]:
        if state.has_transcript:
            transcript_segments = self._artifact_repository.load_transcripts(
                video.id,
                stt_model_version=target_stt_model_version,
            )
        else:
            transcript_segments = []

        if not transcript_segments:
            stt_result = await self._stt_adapter.transcribe(audio_path=str(audio), trace_id=trace_id)
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
            self._artifact_repository.replace_transcripts(
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
        sem = asyncio.Semaphore(_CHUNK_CONCURRENCY)

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
                keyframe_asset_id = self._artifact_repository.upsert_asset(
                    video.id,
                    AssetRecord(
                        asset_type="KEYFRAME",
                        storage_path=keyframe_storage_path,
                        start_ms=draft.start_ms,
                        end_ms=draft.end_ms,
                    ),
                )

                await self._assert_not_deleting(video.id)

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

        results = await asyncio.gather(*[process_one(d) for d in chunk_drafts])
        return sorted(results, key=lambda c: c.chunk_index)

    async def _batch_embed(self, chunks: list[ChunkRecord], trace_id: str) -> list[list[float]]:
        embeddings: list[list[float]] = []
        for offset in range(0, len(chunks), self._embedding_batch_size):
            batch = chunks[offset : offset + self._embedding_batch_size]
            batch_result = await self._embedding_client.embed_texts(
                [chunk.enriched_text for chunk in batch],
                trace_id=trace_id,
            )
            embeddings.extend(batch_result.embeddings)
        return embeddings

    def _persist_results(
        self,
        video_id: str,
        chunks: list[ChunkRecord],
        embeddings: list[list[float]],
        *,
        set_ready: bool,
    ) -> None:
        self._artifact_repository.persist_chunks_and_vectors(
            video_id,
            chunks=chunks,
            embeddings=embeddings,
            set_ready=set_ready,
        )

    async def _assert_not_deleting(self, video_id: str) -> None:
        if self._video_repository.is_deleting(video_id):
            raise DeleteRequested(video_id)
