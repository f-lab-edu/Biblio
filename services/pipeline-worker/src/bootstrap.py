"""Production dependency assembly for the pipeline worker.

Builds all real adapters, repositories, use cases, and the consumer,
then returns a ConsumerBootstrap that runs forever.
"""

import asyncio
import signal
from dataclasses import dataclass
from typing import Any

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from src.infra.ai.embedding_client import EmbeddingClient
from src.infra.ai.gemini_vision_adapter import GeminiVisionAdapter
from src.infra.ai.google_stt_adapter import GoogleSTTAdapter
from src.infra.ai.stt_batch_callable import build_stt_callable
from src.infra.db.artifact_repository import ArtifactRepository
from src.infra.db.embedding_repository import SqlAlchemyEmbeddingRepository
from src.infra.db.enrichment_repository import SqlAlchemyEnrichmentRepository
from src.infra.db.normalization_repository import NormalizationRepository
from src.infra.db.pipeline_work_repository import PipelineWorkRepository
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchUnitOfWork,
)
from src.infra.db.release_repository import ReleaseContextRepository
from src.infra.db.stage_message_guard import SqlAlchemyStageMessageClaimer
from src.infra.db.transcript_assembly import TranscriptAssemblyCoordinator
from src.infra.db.transcription_repository import TranscriptionRepository
from src.infra.db.video_repository import VideoRepository
from src.infra.media.ffmpeg_client import FFmpegClient
from src.infra.media.youtube_downloader import YtDlpYoutubeDownloader
from src.infra.queue.consumer import (
    PipelineWorkerConsumer,
    StageDispatchContext,
    StageHandlerResult,
)
from src.infra.queue.inmemory_broker import InMemoryBrokerClient
from src.infra.queue.pgmq_client import PGMQBrokerClient
from src.infra.queue.transactional_pgmq import TransactionalPGMQPublisher
from src.infra.storage.gcs_client import GCSStorageClient
from src.config.settings import Settings
from src.schemas.messages import (
    EmbedBatchMessage,
    EnrichChunkMessage,
    MessageType,
    NormalizeVideoMessage,
    TranscribePartMessage,
)
from src.services.chunking_service import ChunkingService
from src.services.embedding_service import EmbeddingBatchService
from src.services.enrichment_service import EnrichmentService
from src.services.normalization_service import NormalizationService
from src.services.pipeline_recovery import (
    PipelineRecoveryCoordinator,
    StageRecoveryPolicy,
)
from src.services.pipeline_work_scheduler import PipelineWorkScheduler
from src.services.transcript_merge_service import TranscriptMergeService
from src.services.transcript_assembly_service import TranscriptAssemblyService
from src.services.transcription_service import TranscriptionService
from src.telemetry.performance_sampler import performance_sampler_coroutines
from src.usecases.delete_project import DeleteProjectUseCase
from src.usecases.delete_video import DeleteVideoUseCase
from src.usecases.start_pipeline import StartPipelineUseCase
from src.utils.logging import get_logger
from src.utils.workdir import WorkdirManager

QUEUE_NAMES = [mt.value for mt in MessageType]
CONSUMER_QUEUE_NAMES = [
    MessageType.PREPROCESS_REQUEST.value,
    MessageType.NORMALIZE_VIDEO.value,
    MessageType.TRANSCRIBE_PART.value,
    MessageType.ENRICH_CHUNK.value,
    MessageType.EMBED_BATCH.value,
    MessageType.DELETE_REQUEST.value,
    MessageType.PROJECT_DELETE_REQUEST.value,
]
STAGE_QUEUE_NAMES = [
    MessageType.NORMALIZE_VIDEO.value,
    MessageType.TRANSCRIBE_PART.value,
    MessageType.ENRICH_CHUNK.value,
    MessageType.EMBED_BATCH.value,
]


def consumer_loop_specs(settings: Settings) -> tuple[tuple[str, int], ...]:
    return (
        (MessageType.PREPROCESS_REQUEST.value, 1),
        (MessageType.NORMALIZE_VIDEO.value, settings.normalization_concurrency),
        (MessageType.TRANSCRIBE_PART.value, settings.stt_part_concurrency),
        (MessageType.ENRICH_CHUNK.value, settings.enrichment_concurrency),
        (MessageType.EMBED_BATCH.value, settings.embedding_concurrency),
        (MessageType.DELETE_REQUEST.value, 1),
        (MessageType.PROJECT_DELETE_REQUEST.value, 1),
    )


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
        MessageType.NORMALIZE_VIDEO.value: (
            settings.normalization_queue_visibility_timeout_sec
        ),
        MessageType.TRANSCRIBE_PART.value: (
            settings.transcription_queue_visibility_timeout_sec
        ),
        MessageType.ENRICH_CHUNK.value: (
            settings.enrichment_queue_visibility_timeout_sec
        ),
        MessageType.EMBED_BATCH.value: settings.embedding_queue_visibility_timeout_sec,
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
    stage_message_claimer = SqlAlchemyStageMessageClaimer(session_factory)
    pipeline_work_repo = PipelineWorkRepository(session_factory)

    consumer_specs = consumer_loop_specs(settings)
    consumer_count = sum(concurrency for _, concurrency in consumer_specs)

    # --- Broker ---
    pgmq_pool = None
    if settings.broker_type == "pgmq":
        dsn = _to_asyncpg_dsn(settings.database_url)
        pgmq_pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=consumer_count + 2,
        )
        await _ensure_pgmq_queues(pgmq_pool, QUEUE_NAMES)
        broker = PGMQBrokerClient(pgmq_pool, _queue_visibility_timeouts(settings))
        transactional_publisher = TransactionalPGMQPublisher()
        log.info("broker=pgmq pool_size={}", consumer_count + 2)
    else:
        broker = InMemoryBrokerClient()
        transactional_publisher = broker
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
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(
            session_factory,
            transactional_publisher,
            embedding_batch_size=settings.embedding_batch_size,
        )
    )
    assembly_boundary = TranscriptAssemblyCoordinator(
        session_factory=session_factory,
        storage=storage_client,
        service=TranscriptAssemblyService(
            merge_service=TranscriptMergeService(),
            chunking_service=chunking_service,
        ),
        target_provider=release_context_repo,
        fallback_embedding_model_version=settings.embedding_model_version,
    )
    normalization_repository = NormalizationRepository(
        session_factory=session_factory,
        publisher=transactional_publisher,
        scheduler=scheduler,
        stt_capacity=settings.stt_part_concurrency,
    )
    normalization_service = NormalizationService(
        media=ffmpeg_client,
        storage=storage_client,
        repository=normalization_repository,
        workdirs=workdir_manager,
        part_duration_ms=settings.audio_part_duration_sec * 1000,
        overlap_ms=settings.audio_part_overlap_sec * 1000,
        frame_interval_ms=settings.frame_candidate_interval_sec * 1000,
        frame_max_width=settings.frame_candidate_max_width,
        stt_model_version=settings.stt_model_version or "chirp_2",
        signed_url_ttl_sec=settings.normalization_signed_url_ttl_sec,
        assembly_boundary=assembly_boundary,
    )
    transcription_repository = TranscriptionRepository(
        session_factory=session_factory,
        publisher=transactional_publisher,
        scheduler=scheduler,
        stt_capacity=settings.stt_part_concurrency,
        assembly_boundary=assembly_boundary,
    )
    transcription_service = TranscriptionService(
        repository=transcription_repository,
        storage=storage_client,
        stt=stt_adapter,
        max_delivery_attempts=settings.stage_max_delivery_attempts,
    )
    enrichment_repository = SqlAlchemyEnrichmentRepository(
        session_factory=session_factory,
        publisher=transactional_publisher,
        scheduler=scheduler,
        enrichment_capacity=settings.enrichment_concurrency,
        embedding_capacity=settings.embedding_concurrency,
        embedding_batch_size=settings.embedding_batch_size,
    )
    enrichment_service = EnrichmentService(
        repository=enrichment_repository,
        storage=storage_client,
        vision=vision_adapter,
        vision_max_retries=settings.max_retries,
        max_delivery_attempts=settings.stage_max_delivery_attempts,
    )
    embedding_repository = SqlAlchemyEmbeddingRepository(
        session_factory=session_factory,
        publisher=transactional_publisher,
        scheduler=scheduler,
        embedding_capacity=settings.embedding_concurrency,
        embedding_batch_size=settings.embedding_batch_size,
    )
    embedding_service = EmbeddingBatchService(
        repository=embedding_repository,
        embedding=embedding_client,
        max_delivery_attempts=settings.stage_max_delivery_attempts,
    )

    delete_uc = DeleteVideoUseCase(
        video_repository=video_repo,
        artifact_repository=artifact_repo,
        storage_client=storage_client,
        pipeline_work_repository=pipeline_work_repo,
    )
    start_pipeline_uc = StartPipelineUseCase(
        video_repository=video_repo,
        work_repository=pipeline_work_repo,
        scheduler=scheduler,
        storage=storage_client,
        youtube_downloader=youtube_downloader,
        workdirs=workdir_manager,
        delete_video=delete_uc,
        pipeline_version=settings.pipeline_version,
        normalization_capacity=settings.normalization_concurrency,
    )
    delete_project_uc = DeleteProjectUseCase(
        video_repository=video_repo,
        delete_video_use_case=delete_uc,
        session_factory=session_factory,
    )

    # --- Consumer ---
    async def normalize_video(context: StageDispatchContext) -> None:
        message = context.message
        if not isinstance(message, NormalizeVideoMessage):
            raise TypeError("NORMALIZE_VIDEO handler received a different message type")
        await normalization_service.execute(message)

    async def transcribe_part(context: StageDispatchContext) -> StageHandlerResult:
        if not isinstance(context.message, TranscribePartMessage):
            raise TypeError("TRANSCRIBE_PART handler received a different message type")
        return await transcription_service.execute(context)

    async def enrich_chunk(context: StageDispatchContext) -> StageHandlerResult:
        if not isinstance(context.message, EnrichChunkMessage):
            raise TypeError("ENRICH_CHUNK handler received a different message type")
        return await enrichment_service.execute(context)

    async def embed_batch(context: StageDispatchContext) -> StageHandlerResult:
        if not isinstance(context.message, EmbedBatchMessage):
            raise TypeError("EMBED_BATCH handler received a different message type")
        return await embedding_service.execute(context)

    consumer = PipelineWorkerConsumer(
        {
            MessageType.PREPROCESS_REQUEST: lambda envelope: start_pipeline_uc.execute(
                video_id=str(envelope.video_ids[0]),
                trace_id=str(envelope.trace_id),
            ),
            MessageType.NORMALIZE_VIDEO: normalize_video,
            MessageType.TRANSCRIBE_PART: transcribe_part,
            MessageType.ENRICH_CHUNK: enrich_chunk,
            MessageType.EMBED_BATCH: embed_batch,
            MessageType.DELETE_REQUEST: lambda envelope: delete_uc.execute(
                video_ids=[str(video_id) for video_id in envelope.video_ids],
                trace_id=str(envelope.trace_id),
            ),
            MessageType.PROJECT_DELETE_REQUEST: lambda envelope: delete_project_uc.execute(
                project_id=str(envelope.project_id),
                trace_id=str(envelope.trace_id),
            ),
        },
        stage_message_claimer=stage_message_claimer,
    )

    log.info(
        "pipeline worker ready consumers={} queues={} stt_model={} embedding_model={}",
        consumer_count,
        CONSUMER_QUEUE_NAMES,
        settings.stt_model_version,
        settings.embedding_model_version,
    )

    # --- Run ---
    sampler_coroutines = performance_sampler_coroutines(
        pgmq_pool=pgmq_pool,
        queue_names=STAGE_QUEUE_NAMES,
        stage_session_factory=session_factory,
        queue_interval_seconds=settings.queue_sample_interval_sec,
        process_interval_seconds=settings.worker_process_sample_interval_sec,
    )
    recovery = PipelineRecoveryCoordinator(
        repository=pipeline_work_repo,
        scheduler=scheduler,
        policies=(
            StageRecoveryPolicy(
                "NORMALIZE_VIDEO",
                settings.normalization_concurrency,
                settings.normalization_queue_visibility_timeout_sec,
            ),
            StageRecoveryPolicy(
                "TRANSCRIBE_PART",
                settings.stt_part_concurrency,
                settings.transcription_queue_visibility_timeout_sec,
            ),
            StageRecoveryPolicy(
                "ENRICH_CHUNK",
                settings.enrichment_concurrency,
                settings.enrichment_queue_visibility_timeout_sec,
            ),
            StageRecoveryPolicy(
                "EMBED_BATCH",
                settings.embedding_concurrency,
                settings.embedding_queue_visibility_timeout_sec,
            ),
        ),
    )
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for shutdown_signal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(shutdown_signal, stop_event.set)
            installed_signals.append(shutdown_signal)
        except (NotImplementedError, RuntimeError):
            pass

    consumer_tasks = [
        asyncio.create_task(
            consumer.run_forever(
                broker,
                [queue_name],
                poll_interval_sec=settings.poll_interval_sec,
                stop_event=stop_event,
            )
        )
        for queue_name, concurrency in consumer_specs
        for _ in range(concurrency)
    ]
    background_tasks = [
        asyncio.create_task(coroutine) for coroutine in sampler_coroutines
    ]
    background_tasks.append(
        asyncio.create_task(
            recovery.run_forever(
                stop_event,
                interval_sec=settings.recovery_scan_interval_sec,
            )
        )
    )
    try:
        await stop_event.wait()
    finally:
        stop_event.set()
        await asyncio.gather(*consumer_tasks, return_exceptions=True)
        for task in background_tasks:
            task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        for shutdown_signal in installed_signals:
            loop.remove_signal_handler(shutdown_signal)
        await ctx.cleanup()
        log.info("pipeline worker shutdown complete")
