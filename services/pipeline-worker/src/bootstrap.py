"""Production dependency assembly for the pipeline worker.

Builds all real adapters, repositories, use cases, and the consumer,
then returns a ConsumerBootstrap that runs forever.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from src.infra.ai.embedding_client import EmbeddingClient
from src.infra.ai.gemini_vision_adapter import GeminiVisionAdapter
from src.infra.ai.google_stt_adapter import GoogleSTTAdapter
from src.infra.ai.stt_batch_callable import build_stt_callable
from src.infra.db.artifact_repository import ArtifactRepository
from src.infra.db.release_repository import ReleaseContextRepository
from src.infra.db.video_repository import VideoRepository
from src.infra.media.ffmpeg_client import FFmpegClient
from src.infra.media.youtube_downloader import YtDlpYoutubeDownloader
from src.infra.queue.consumer import PipelineWorkerConsumer
from src.infra.queue.inmemory_broker import InMemoryBrokerClient
from src.infra.queue.pgmq_client import PGMQBrokerClient
from src.infra.storage.gcs_client import GCSStorageClient
from src.config.settings import Settings
from src.schemas.messages import MessageType
from src.services.chunking_service import ChunkingService
from src.services.long_audio_transcription import LongAudioTranscriptionService
from src.services.pipeline_orchestrator import PipelineOrchestrator
from src.services.transcript_merge_service import TranscriptMergeService
from src.telemetry.performance_sampler import performance_sampler_coroutines
from src.usecases.delete_project import DeleteProjectUseCase
from src.usecases.delete_video import DeleteVideoUseCase
from src.usecases.process_video import ProcessVideoUseCase
from src.utils.logging import get_logger
from src.utils.workdir import WorkdirManager

QUEUE_NAMES = [mt.value for mt in MessageType]


@dataclass(slots=True)
class ProductionContext:
    engine: AsyncEngine
    pgmq_pool: Any | None
    closers: tuple[Any, ...] = ()

    async def cleanup(self) -> None:
        await self.engine.dispose()
        if self.pgmq_pool is not None:
            await self.pgmq_pool.close()
        for closer in self.closers:
            aclose = getattr(closer, "aclose", None)
            if aclose is not None:
                await aclose()


def _to_asyncpg_dsn(database_url: str) -> str:
    """Convert SQLAlchemy async DSN to plain asyncpg DSN."""
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def _validate_recovery_timeouts(
    *,
    stale_processing_reclaim_sec: int,
    queue_visibility_timeout_sec: int,
) -> None:
    if stale_processing_reclaim_sec >= queue_visibility_timeout_sec:
        raise ValueError(
            "STALE_PROCESSING_RECLAIM_SEC must be less than "
            "QUEUE_VISIBILITY_TIMEOUT_SEC"
        )


def _queue_visibility_timeouts(settings: Settings) -> dict[str, int]:
    return {
        MessageType.PREPROCESS_REQUEST.value: settings.queue_visibility_timeout_sec,
        MessageType.DELETE_REQUEST.value: settings.delete_queue_visibility_timeout_sec,
        MessageType.PROJECT_DELETE_REQUEST.value: settings.delete_queue_visibility_timeout_sec,
    }


async def _ensure_pgmq_queues(pool: Any, queue_names: list[str]) -> None:
    async with pool.acquire() as conn:
        for name in queue_names:
            try:
                await conn.execute("SELECT pgmq.create($1)", name)
            except asyncpg.UniqueViolationError:
                pass


async def create_production_bootstrap(settings: Settings) -> None:
    """Build all production dependencies and run the consumer loop forever."""
    _validate_recovery_timeouts(
        stale_processing_reclaim_sec=settings.stale_processing_reclaim_sec,
        queue_visibility_timeout_sec=settings.queue_visibility_timeout_sec,
    )
    log = get_logger().bind(trace_id="-", video_id="-", user_id="-")

    # --- DB ---
    engine = create_async_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    video_repo = VideoRepository(
        session_factory,
        settings.stale_processing_reclaim_sec,
    )
    artifact_repo = ArtifactRepository(session_factory)
    release_context_repo = ReleaseContextRepository(session_factory)

    # --- Broker ---
    pgmq_pool = None
    if settings.broker_type == "pgmq":
        dsn = _to_asyncpg_dsn(settings.database_url)
        pgmq_pool = await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=settings.worker_concurrency + 2)
        await _ensure_pgmq_queues(pgmq_pool, QUEUE_NAMES)
        broker = PGMQBrokerClient(pgmq_pool, _queue_visibility_timeouts(settings))
        log.info("broker=pgmq pool_size={}", settings.worker_concurrency + 2)
    else:
        broker = InMemoryBrokerClient()
        log.info("broker=inmemory")

    # --- Storage ---
    from google.cloud import storage as gcs_storage

    gcs_client = gcs_storage.Client(project=settings.gcp_project_id)
    storage_client = GCSStorageClient(
        bucket_factory=lambda: gcs_client.bucket(settings.gcs_video_bucket_name),
        bucket_name=settings.gcs_video_bucket_name,
    )

    # --- STT ---
    stt_callable = build_stt_callable(
        project_id=settings.gcp_project_id,
        location=settings.stt_location,
        recognizer=settings.stt_recognizer,
        model=settings.stt_model_version or "chirp_2",
        submit_timeout_sec=settings.stt_submit_timeout_sec,
        operation_timeout_sec=settings.stt_operation_timeout_sec,
    )
    stt_adapter = GoogleSTTAdapter(
        client=stt_callable,
        max_retries=settings.max_retries,
    )

    # --- Vision ---
    vision_adapter = GeminiVisionAdapter(
        project_id=settings.gcp_project_id,
        location=settings.vision_location,
        model=settings.vision_model,
        timeout_sec=settings.vision_timeout_sec,
        max_output_tokens=settings.vision_max_output_tokens,
    )

    # --- Embedding ---
    embedding_client = EmbeddingClient(
        base_url=settings.embedding_api_url,
        timeout_sec=settings.embedding_timeout_sec,
        max_retries=settings.max_retries,
        model_version=settings.embedding_model_version,
    )
    ctx = ProductionContext(
        engine=engine,
        pgmq_pool=pgmq_pool,
        closers=(vision_adapter, embedding_client),
    )

    # --- Services ---
    ffmpeg_client = FFmpegClient()
    youtube_downloader = YtDlpYoutubeDownloader(
        max_duration_sec=settings.youtube_max_duration_sec,
        max_filesize_bytes=settings.youtube_max_filesize_bytes,
        max_height=settings.youtube_max_height,
        timeout_sec=settings.download_timeout_sec,
        proxy_url=settings.youtube_proxy_url,
    )
    workdir_manager = WorkdirManager()
    chunking_service = ChunkingService(
        max_tokens=settings.chunk_max_tokens,
        overlap_sentences=settings.chunk_overlap_sentences,
    )
    long_audio_transcription_service = LongAudioTranscriptionService(
        artifact_repository=artifact_repo,
        video_repository=video_repo,
        storage_client=storage_client,
        ffmpeg_client=ffmpeg_client,
        stt_adapter=stt_adapter,
        merge_service=TranscriptMergeService(),
        part_duration_sec=settings.audio_part_duration_sec,
        part_overlap_sec=settings.audio_part_overlap_sec,
        stt_concurrency=settings.stt_part_concurrency,
        processing_timeout_sec=settings.audio_processing_timeout_sec,
    )

    orchestrator = PipelineOrchestrator(
        video_repository=video_repo,
        artifact_repository=artifact_repo,
        storage_client=storage_client,
        youtube_downloader=youtube_downloader,
        ffmpeg_client=ffmpeg_client,
        stt_adapter=stt_adapter,
        embedding_client=embedding_client,
        vision_adapter=vision_adapter,
        workdir_manager=workdir_manager,
        chunking_service=chunking_service,
        long_audio_transcription_service=long_audio_transcription_service,
        embedding_batch_size=settings.embedding_batch_size,
        stt_model_version=settings.stt_model_version or "chirp_2",
        embedding_model_version=settings.embedding_model_version,
        release_context_repository=release_context_repo,
        max_audio_duration_sec=settings.max_audio_duration_sec,
        max_source_size_bytes=settings.youtube_max_filesize_bytes,
        audio_processing_timeout_sec=settings.audio_processing_timeout_sec,
    )

    delete_uc = DeleteVideoUseCase(
        video_repository=video_repo,
        artifact_repository=artifact_repo,
        storage_client=storage_client,
    )
    process_uc = ProcessVideoUseCase(
        video_repository=video_repo,
        orchestrator=orchestrator,
        delete_video_use_case=delete_uc,
        stt_model_version=settings.stt_model_version or "chirp_2",
        embedding_model_version=settings.embedding_model_version,
    )
    delete_project_uc = DeleteProjectUseCase(
        video_repository=video_repo,
        delete_video_use_case=delete_uc,
        session_factory=session_factory,
    )

    # --- Consumer ---
    consumer = PipelineWorkerConsumer({
        MessageType.PREPROCESS_REQUEST: lambda envelope: process_uc.execute(
            video_id=str(envelope.video_ids[0]),
            trace_id=str(envelope.trace_id),
        ),
        MessageType.DELETE_REQUEST: lambda envelope: delete_uc.execute(
            video_ids=[str(video_id) for video_id in envelope.video_ids],
            trace_id=str(envelope.trace_id),
        ),
        MessageType.PROJECT_DELETE_REQUEST: lambda envelope: delete_project_uc.execute(
            project_id=str(envelope.project_id),
            trace_id=str(envelope.trace_id),
        ),
    })

    log.info(
        "pipeline worker ready  concurrency={} queues={} stt_model={} embedding_model={}",
        settings.worker_concurrency,
        QUEUE_NAMES,
        settings.stt_model_version,
        settings.embedding_model_version,
    )

    # --- Run ---
    sampler_coroutines = performance_sampler_coroutines(
        pgmq_pool=pgmq_pool,
        queue_name=MessageType.PREPROCESS_REQUEST.value,
        queue_interval_seconds=settings.queue_sample_interval_sec,
        process_interval_seconds=settings.worker_process_sample_interval_sec,
    )
    try:
        await asyncio.gather(
            *[
                consumer.run_forever(
                    broker,
                    QUEUE_NAMES,
                    poll_interval_sec=settings.poll_interval_sec,
                )
                for _ in range(settings.worker_concurrency)
            ],
            *sampler_coroutines,
        )
    finally:
        await ctx.cleanup()
        log.info("pipeline worker shutdown complete")
