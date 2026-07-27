from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from sqlalchemy.ext.asyncio import AsyncEngine

from src.config.settings import Settings
from src.dataset.batch import DatasetBatchService
from src.dataset.manifest import DatasetManifestSelector
from src.evaluation.artifacts import EvaluationDetailArtifactWriter
from src.evaluation.dataset_loader import EvaluationDatasetLoader
from src.evaluation.local_search import LocalEmbeddingSearchBackend
from src.evaluation.model_artifacts import LocalScoringModelArtifactLoader
from src.evaluation.recorder import EvaluationRecorder
from src.evaluation.service import OfflineEvaluationService
from src.evaluation.evaluator import OfflineEvaluator
from src.infra.db.legacy_reindex_lock import PostgresAdvisoryLegacyReindexLock
from src.infra.db.legacy_reindex_store import LegacyReindexStore, VectorIndexCatalogStore
from src.infra.db.session import create_db_engine, create_session_factory
from src.infra.db.snapshot_restore import CatalogSnapshotIndexRestore
from src.infra.db.stores import (
    DbChunkTextSnapshot,
    MLPipelineRunStore,
    ModelReleaseStore,
    ProjectRollbackStore,
)
from src.infra.storage.client import ArtifactStore
from src.infra.storage.gcs import GCSArtifactStore
from src.infra.storage.local import LocalArtifactStore
from src.release.candidate_deployment import CandidateDeploymentService
from src.release.legacy_reindex import LegacyReindexCoordinator, LegacyReindexScheduler
from src.release.model_reload import (
    ManagedEmbeddingModelReloadClient,
    ManagedEmbeddingModelReloadFanout,
)
from src.release.readiness import (
    ManagedEmbeddingReadinessClient,
    ManagedEmbeddingReadinessFanout,
)
from src.release.rollback import RollbackRequestMessage, RollbackTransitionManager
from src.release.serving_reload import (
    NoopServingTargetReloader,
    SearchServiceServingTargetReloader,
    ServingTargetReloader,
    SqlAlchemyReleaseChangeCommitter,
)
from src.release.transition import ServingTransitionManager
from src.release.video_reembed import VideoReembedService
from src.run_control.consumer import TrainingRequestHandler, RunFlowExecutor
from src.run_control.db_slots import DbRunSlotStore
from src.runtime.consumer import QueueMessageConsumer
from src.runtime.embedding import ManagedEmbeddingBatchClient
from src.runtime.messages import DatasetGenerationRequest, ReembeddingRequest
from src.runtime.queue import (
    BrokerClient,
    InMemoryBrokerClient,
    PGMQBrokerClient,
    ensure_pgmq_queues,
    to_asyncpg_dsn,
)
from src.runtime.scheduler import FeedbackLoopScheduler
from src.training.runner import LocalTrainingRunner


RoleBootstrap = Callable[[Settings], Awaitable[None] | None] | Callable[[Settings, bool], Awaitable[None] | None]


@dataclass(slots=True)
class RuntimeContext:
    engine: AsyncEngine
    pgmq_pool: Any | None

    async def cleanup(self) -> None:
        await self.engine.dispose()
        if self.pgmq_pool is not None:
            await self.pgmq_pool.close()


def create_role_bootstraps() -> Mapping[str, RoleBootstrap]:
    return {
        "scheduler": bootstrap_scheduler,
        "dataset-worker": bootstrap_dataset_worker,
        "train-release-worker": bootstrap_train_release_worker,
        "rollback-worker": bootstrap_rollback_worker,
        "legacy-reindex-worker": bootstrap_legacy_reindex_worker,
        "reembedding-worker": bootstrap_reembedding_worker,
    }


async def bootstrap_scheduler(settings: Settings, *, run_once: bool) -> None:
    ctx, broker, session_factory = await _build_runtime_context(settings)
    try:
        async with session_factory() as session:
            scheduler = FeedbackLoopScheduler(
                broker=broker,
                reconciliation=ReconciliationServiceAdapter(session),
                recovery=RollbackRecoveryAdapter(session, broker, settings),
                candidate_deployment=CandidateDeploymentRetryAdapter(session, settings),
                dataset_queue_name=settings.feedback_dataset_queue_name,
                training_queue_name=settings.feedback_training_queue_name,
                stuck_run_timeout_sec=settings.stuck_run_timeout_sec,
                rollback_timeout_sec=settings.rollback_restore_timeout_sec,
                dataset_hour_kst=settings.dataset_generation_hour_kst,
                dataset_minute_kst=settings.dataset_generation_minute_kst,
                training_weekday_kst=settings.training_request_weekday_kst,
                training_hour_kst=settings.training_request_hour_kst,
                training_minute_kst=settings.training_request_minute_kst,
            )
            if run_once:
                await scheduler.run_once()
                return
            await scheduler.run_forever(tick_interval_sec=settings.scheduler_tick_interval_sec)
    finally:
        await ctx.cleanup()


async def bootstrap_dataset_worker(settings: Settings, *, run_once: bool) -> None:
    ctx, broker, session_factory = await _build_runtime_context(settings)
    raw_feedback_log_store = _build_raw_feedback_log_store(settings)
    artifact_store = _build_artifact_store(settings)
    workspace_dir = _workspace_dir(settings, "dataset-worker")
    try:
        async def handle(payload: dict[str, Any]) -> None:
            message = DatasetGenerationRequest.model_validate(payload)
            source_window_end = message.source_window_end or message.issued_at
            source_window_start = message.source_window_start or source_window_end - timedelta(days=30)
            async with session_factory() as session:
                service = DatasetBatchService(
                    raw_feedback_log_store=raw_feedback_log_store,
                    artifact_store=artifact_store,
                    chunk_text_snapshot=DbChunkTextSnapshot(session),
                )
                await service.materialize_latest(
                    raw_feedback_log_prefix=settings.raw_feedback_log_prefix,
                    dataset_artifact_prefix=settings.dataset_artifact_prefix,
                    workspace_dir=workspace_dir,
                    source_window_start=source_window_start,
                    source_window_end=source_window_end,
                )

        consumer = QueueMessageConsumer({"DATASET_GENERATION_REQUEST": handle})
        await _run_consumer(
            consumer,
            broker,
            settings.feedback_dataset_queue_name,
            run_once=run_once,
            poll_interval_sec=settings.worker_poll_interval_sec,
        )
    finally:
        await ctx.cleanup()


async def bootstrap_train_release_worker(settings: Settings, *, run_once: bool) -> None:
    ctx, broker, session_factory = await _build_runtime_context(settings)
    artifact_store = _build_artifact_store(settings)
    workspace_dir = _workspace_dir(settings, "train-release-worker")
    try:
        async def handle(payload: dict[str, Any]) -> None:
            async with session_factory() as session:
                release_store = ModelReleaseStore(session)
                transition_manager = ServingTransitionManager(
                    run_store=MLPipelineRunStore(session),
                    release_store=release_store,
                    readiness=_build_embedding_readiness(settings),
                    legacy_reindex_gate=LegacyReindexStore(session),
                    release_change_committer=SqlAlchemyReleaseChangeCommitter(session),
                    serving_target_reloader=_build_serving_target_reloader(settings),
                )
                run_executor = RunFlowExecutor(
                    session=session,
                    dataset_artifact_uri_prefix=settings.dataset_artifact_prefix,
                    training_runner=LocalTrainingRunner(
                        artifact_store=artifact_store,
                        model_artifact_prefix=settings.model_artifact_prefix,
                        base_model_name=settings.local_training_model_name,
                        embedding_dimension=settings.embedding_dimension,
                        serving_model_artifact_prefix=settings.serving_model_artifact_prefix,
                    ),
                    evaluator=OfflineEvaluationService(
                        dataset_loader=EvaluationDatasetLoader(artifact_store),
                        evaluator=OfflineEvaluator(
                            LocalEmbeddingSearchBackend(dimensions=settings.embedding_dimension)
                        ),
                        workspace_dir=workspace_dir,
                        model_artifact_loader=LocalScoringModelArtifactLoader(
                            artifact_store=artifact_store,
                            model_artifact_prefix=settings.model_artifact_prefix,
                        ),
                    ),
                    evaluation_recorder=EvaluationRecorder(
                        session=session,
                        detail_writer=EvaluationDetailArtifactWriter(
                            artifact_store=artifact_store,
                            evaluation_artifact_prefix=settings.evaluation_artifact_prefix,
                        ),
                        workspace_dir=workspace_dir,
                    ),
                    handoff_sink=CandidateDeploymentHandoffSink(
                        CandidateDeploymentService(
                            run_store=MLPipelineRunStore(session),
                            transition_manager=transition_manager,
                            reload_client=_build_embedding_model_reload(settings),
                            max_attempts=settings.candidate_deployment_max_attempts,
                        )
                    ),
                    evaluation_dataset_ref=settings.evaluation_dataset_ref,
                    training_config_ref=settings.training_config_path,
                    training_config_hash=settings.training_config_path,
                    workspace_dir=workspace_dir,
                )
                handler = TrainingRequestHandler(
                    manifest_selector=DatasetManifestSelector(artifact_store),
                    dataset_artifact_prefix=settings.dataset_artifact_prefix,
                    run_slot_store=DbRunSlotStore(
                        session,
                        model_version_prefix=settings.model_version_prefix,
                    ),
                    model_release_store=release_store,
                    run_executor=run_executor,
                    workspace_dir=workspace_dir,
                )
                await handler.handle(payload)
                await session.commit()

        consumer = QueueMessageConsumer({"TRAINING_REQUEST": handle})
        await _run_consumer(
            consumer,
            broker,
            settings.feedback_training_queue_name,
            run_once=run_once,
            poll_interval_sec=settings.worker_poll_interval_sec,
        )
    finally:
        await ctx.cleanup()


async def bootstrap_rollback_worker(settings: Settings, *, run_once: bool) -> None:
    ctx, broker, session_factory = await _build_runtime_context(settings)
    try:
        async def handle(payload: dict[str, Any]) -> None:
            message = RollbackRequestMessage.model_validate(payload)
            async with session_factory() as session:
                manager = RollbackTransitionManager(
                    release_store=ModelReleaseStore(session),
                    project_store=ProjectRollbackStore(session),
                    target_readiness=_build_embedding_readiness(settings),
                    index_restore=CatalogSnapshotIndexRestore(session),
                    release_change_committer=SqlAlchemyReleaseChangeCommitter(session),
                    serving_target_reloader=_build_serving_target_reloader(settings),
                )
                await manager.handle_request(message)
                await session.commit()

        consumer = QueueMessageConsumer({"ROLLBACK_REQUEST": handle})
        await _run_consumer(
            consumer,
            broker,
            settings.feedback_rollback_queue_name,
            run_once=run_once,
            poll_interval_sec=settings.worker_poll_interval_sec,
        )
    finally:
        await ctx.cleanup()


async def bootstrap_reembedding_worker(settings: Settings, *, run_once: bool) -> None:
    ctx, broker, session_factory = await _build_runtime_context(settings)
    try:
        async def handle(payload: dict[str, Any]) -> None:
            message = ReembeddingRequest.model_validate(payload)
            async with session_factory() as session:
                service = VideoReembedService(
                    session=session,
                    embedding_client=ManagedEmbeddingBatchClient(
                        base_url=settings.batch_embedding_endpoint_url,
                        timeout_sec=settings.training_timeout_sec,
                        default_model_version=settings.local_training_model_name,
                    ),
                    batch_size=settings.rollback_reembed_batch_size,
                )
                await service.reembed_video(
                    video_id=message.video_id,
                    target_model_version=message.target_model_version,
                    target_index_name=message.target_index_name,
                    trace_id=message.trace_id,
                )
                await session.commit()

        consumer = QueueMessageConsumer({"REEMBEDDING_REQUEST": handle})
        await _run_consumer(
            consumer,
            broker,
            settings.feedback_reembedding_queue_name,
            run_once=run_once,
            poll_interval_sec=settings.worker_poll_interval_sec,
        )
    finally:
        await ctx.cleanup()


async def bootstrap_legacy_reindex_worker(settings: Settings, *, run_once: bool) -> None:
    ctx, _broker, session_factory = await _build_runtime_context(settings)
    try:
        async with session_factory() as session:
            scheduler = LegacyReindexScheduler(
                coordinator=LegacyReindexCoordinator(
                    legacy_store=LegacyReindexStore(session),
                    catalog_store=VectorIndexCatalogStore(session),
                    embedding_client=ManagedEmbeddingBatchClient(
                        base_url=settings.batch_embedding_endpoint_url,
                        timeout_sec=settings.training_timeout_sec,
                        default_model_version=settings.local_training_model_name,
                    ),
                    batch_size=settings.legacy_reindex_batch_size,
                    per_run_video_limit=settings.legacy_reindex_per_run_video_limit,
                    throttle_sleep_ms=settings.legacy_reindex_throttle_sleep_ms,
                    release_store=ModelReleaseStore(session),
                    embedding_dimension=settings.embedding_dimension,
                    project_store=ProjectRollbackStore(session),
                ),
                lock=PostgresAdvisoryLegacyReindexLock(session),
                scan_interval_sec=settings.legacy_reindex_scan_interval_sec,
            )
            if run_once:
                await scheduler.run_once(trace_id=_new_trace_id())
                await session.commit()
                return
            stop_event = asyncio.Event()
            await scheduler.run_until_stopped(stop_event=stop_event)
    finally:
        await ctx.cleanup()


class CandidateDeploymentHandoffSink:
    def __init__(self, deployment: CandidateDeploymentService) -> None:
        self._deployment = deployment

    async def ready_for_release(self, *, run_id: UUID, trace_id: UUID) -> None:
        await self._deployment.attempt(run_id=run_id, trace_id=trace_id)


class ReconciliationServiceAdapter:
    def __init__(self, session) -> None:
        from src.run_control.reconciliation import ReconciliationService

        self._service = ReconciliationService(session=session)

    async def inspect(self, *, stuck_run_timeout_sec: int, rollback_timeout_sec: int) -> object:
        return await self._service.inspect(
            stuck_run_timeout_sec=stuck_run_timeout_sec,
            rollback_timeout_sec=rollback_timeout_sec,
        )


class RollbackRecoveryAdapter:
    def __init__(self, session, broker, settings) -> None:
        self._session = session
        self._broker = broker
        self._settings = settings

    async def scan_and_recover(self) -> None:
        from datetime import UTC, datetime

        from src.infra.db.stores import ModelReleaseStore, ProjectRollbackStore
        from src.release.reembed_sink import BrokerReembeddingSink
        from src.release.recovery import RollbackRecoveryService

        release = await ModelReleaseStore(self._session).get_current()
        if release is None or release.release_status != "STABLE":
            return
        project_store = ProjectRollbackStore(self._session)
        if not await project_store.has_rollback_excluded_projects():
            return

        sink = BrokerReembeddingSink(
            broker=self._broker, queue_name=self._settings.feedback_reembedding_queue_name
        )
        await RollbackRecoveryService(
            project_store=project_store, reembedding_sink=sink
        ).dispatch_restored_reembedding(
            active_model_version=release.active_model_version,
            active_index_name=release.active_index_name,
        )
        await project_store.reenter_restored_projects(
            active_model_version=release.active_model_version,
            active_index_name=release.active_index_name,
            updated_at=datetime.now(UTC),
        )
        await self._session.commit()


class CandidateDeploymentRetryAdapter:
    def __init__(self, session, settings) -> None:
        self._session = session
        self._settings = settings

    async def scan_and_deploy(self) -> None:
        run_store = MLPipelineRunStore(self._session)
        run = await run_store.get_candidate_deployment_run()
        if run is None:
            return
        transition_manager = ServingTransitionManager(
            run_store=run_store,
            release_store=ModelReleaseStore(self._session),
            readiness=_build_embedding_readiness(self._settings),
            legacy_reindex_gate=LegacyReindexStore(self._session),
            release_change_committer=SqlAlchemyReleaseChangeCommitter(self._session),
            serving_target_reloader=_build_serving_target_reloader(self._settings),
        )
        await CandidateDeploymentService(
            run_store=run_store,
            transition_manager=transition_manager,
            reload_client=_build_embedding_model_reload(self._settings),
            max_attempts=self._settings.candidate_deployment_max_attempts,
        ).attempt(run_id=run.id, trace_id=_new_trace_id())
        await self._session.commit()


def _build_serving_target_reloader(settings: Settings) -> ServingTargetReloader:
    if settings.search_service_url:
        return SearchServiceServingTargetReloader(base_url=settings.search_service_url)
    return NoopServingTargetReloader()


def _build_embedding_readiness(
    settings: Settings,
) -> ManagedEmbeddingReadinessFanout:
    return ManagedEmbeddingReadinessFanout(
        batch_client=ManagedEmbeddingReadinessClient(
            base_url=settings.batch_embedding_endpoint_url,
        ),
        search_client=ManagedEmbeddingReadinessClient(
            base_url=settings.search_embedding_endpoint_url,
        ),
    )


def _build_embedding_model_reload(
    settings: Settings,
) -> ManagedEmbeddingModelReloadFanout:
    return ManagedEmbeddingModelReloadFanout(
        batch_client=ManagedEmbeddingModelReloadClient(
            base_url=settings.batch_embedding_endpoint_url,
            timeout_sec=settings.training_timeout_sec,
        ),
        search_client=ManagedEmbeddingModelReloadClient(
            base_url=settings.search_embedding_endpoint_url,
            timeout_sec=settings.training_timeout_sec,
        ),
    )


async def _run_consumer(
    consumer: QueueMessageConsumer,
    broker: BrokerClient,
    queue_name: str,
    *,
    run_once: bool,
    poll_interval_sec: float,
) -> None:
    if run_once:
        await consumer.run_once(broker, queue_name)
        return
    await consumer.run_forever(broker, queue_name, poll_interval_sec=poll_interval_sec)


async def _build_runtime_context(settings: Settings):
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    pgmq_pool = None
    if settings.broker_type == "pgmq":
        pgmq_pool = await asyncpg.create_pool(
            dsn=to_asyncpg_dsn(settings.database_url),
            min_size=1,
            max_size=settings.worker_concurrency + 2,
        )
        await ensure_pgmq_queues(
            pgmq_pool,
            [
                settings.feedback_dataset_queue_name,
                settings.feedback_training_queue_name,
                settings.feedback_rollback_queue_name,
                settings.feedback_reembedding_queue_name,
            ],
        )
        broker: BrokerClient = PGMQBrokerClient(pgmq_pool)
    else:
        broker = InMemoryBrokerClient()
    return RuntimeContext(engine=engine, pgmq_pool=pgmq_pool), broker, session_factory


def _build_artifact_store(settings: Settings) -> ArtifactStore:
    if settings.artifact_store_backend == "gcs":
        return GCSArtifactStore(bucket_name=settings.gcs_ml_artifact_bucket_name or "")
    return _build_local_artifact_store(settings)


def _build_raw_feedback_log_store(settings: Settings) -> ArtifactStore:
    if settings.artifact_store_backend == "gcs":
        return GCSArtifactStore(bucket_name=settings.gcs_feedback_log_bucket_name or "")
    return _build_local_artifact_store(settings)


def _build_local_artifact_store(settings: Settings) -> ArtifactStore:
    return LocalArtifactStore(root_dir=Path(settings.local_artifact_root))


def _workspace_dir(settings: Settings, role: str) -> Path:
    path = Path(settings.local_artifact_root) / "workspace" / role
    path.mkdir(parents=True, exist_ok=True)
    return path


def _new_trace_id():
    return uuid4()
