import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.dataset.artifacts import DatasetArtifactRefs
from src.dataset.batch import DatasetBatchService
from src.dataset.manifest import DatasetEligibilityPolicy, DatasetManifestSelector
from src.dataset.materializer import DatasetMaterializer
from src.evaluation.artifacts import EvaluationDetailArtifactWriter
from src.evaluation.dataset_loader import EvaluationDatasetLoader
from src.evaluation.evaluator import OfflineEvaluator
from src.evaluation.local_search import LocalEmbeddingSearchBackend
from src.evaluation.model_artifacts import LocalScoringModelArtifactLoader
from src.evaluation.recorder import EvaluationRecorder
from src.evaluation.service import OfflineEvaluationService
from src.infra.db.models import (
    Base,
    ChunkModel,
    MLPipelineRunModel,
    ModelEvaluationModel,
    ModelReleaseModel,
    ProjectModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.infra.db.stores import (
    MLPipelineRunStore,
    ModelReleaseStore,
    ProjectRollbackStore,
)
from src.infra.storage.inmemory import InMemoryArtifactStore
from src.release.recovery import RollbackRecoveryService
from src.release.rollback import AlwaysReadyRollbackTarget, ImmediateIndexRestore, RollbackTransitionManager
from src.release.transition import CandidateReleaseHandoffSink, ServingTransitionManager
from src.run_control.consumer import RunFlowExecutor, TrainingRequestHandler
from src.run_control.db_slots import DbRunSlotStore
from src.training.manifest import ModelArtifactManifest
from src.training.runner import LocalTrainingRunner


MODEL_VERSION_PREFIX = "bge-m3"


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 13, 9, 0, tzinfo=UTC)


class _ChunkTextSnapshot:
    async def text_by_chunk_id(self, chunk_ids: set[str]) -> Mapping[str, str]:
        # Async to satisfy the dataset batch snapshot port.
        texts = {
            "chunk_pos": "semantic search ranking",
            "chunk_neg": "unrelated cooking recipe",
        }
        return {chunk_id: texts[chunk_id] for chunk_id in chunk_ids if chunk_id in texts}

    async def random_negative_pool(
        self,
        project_ids: set[str],
        excluded_chunk_ids: Mapping[str, set[str]],
        limit_per_project: int,
    ) -> Mapping[str, Mapping[str, str]]:
        # Async to satisfy the dataset batch snapshot port.
        assert limit_per_project == 10
        if "project-smoke-1" not in project_ids:
            return {}
        assert excluded_chunk_ids == {"project-smoke-1": {"chunk_pos", "chunk_neg"}}
        return {
            "project-smoke-1": {
                "chunk_random": "same project archive note",
            }
        }


class _RecordingReembeddingSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def request_reembedding(
        self,
        *,
        video_id: UUID,
        target_model_version: str,
        target_index_name: str,
    ) -> None:
        # Async to satisfy the rollback recovery sink contract.
        self.calls.append(
            {
                "video_id": video_id,
                "target_model_version": target_model_version,
                "target_index_name": target_index_name,
            }
        )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


async def test_dataset_generation_smoke_materializes_raw_feedback(tmp_path) -> None:
    store = _smoke_store(uuid4())

    dataset_refs = await _materialize_dataset(store, tmp_path)
    manifest = json.loads(store.objects[dataset_refs.manifest_storage_path])
    rows = [
        json.loads(line)
        for line in store.objects[dataset_refs.rows_storage_path].decode().splitlines()
    ]

    assert dataset_refs.dataset_version == "dataset-20260513T090000Z"
    assert manifest["generation_rule_version"] == "retrieval-group-v1"
    assert manifest["training_group_count"] == 1
    assert manifest["negative_source_counts"] == {
        "exposed_unused": 1,
        "random_same_project": 1,
    }
    assert manifest["eligible"] is False
    assert rows[0]["positives"][0]["text"] == "semantic search ranking"
    assert rows[0]["negatives"][0]["text"] == "unrelated cooking recipe"
    assert rows[0]["negatives"][1]["text"] == "same project archive note"


async def test_training_smoke_deploys_candidate_after_offline_evaluation(
    session: AsyncSession,
    tmp_path,
) -> None:
    store, dataset_refs, run, evaluation, release = await _open_candidate_release(session, tmp_path)
    manifest = ModelArtifactManifest.from_json(
        store.objects[f"feedback/models/{run.candidate_model_version}/model_manifest.json"]
    )

    cutover = await _serving_manager(session).cutover_candidate_release(
        run_id=run.id,
        trace_id=uuid4(),
    )

    assert run.status == "DEPLOY_COMPLETED"
    assert run.dataset_version == dataset_refs.dataset_version
    assert run.baseline_model_version == "baseline-v1"
    assert evaluation.overall_decision == "PASS"
    assert manifest.dataset_version == dataset_refs.dataset_version
    assert store.objects[f"models/{run.candidate_model_version}/config.json"] == b"{}"
    assert cutover.status == "cutover"
    assert run.cutover_time is not None
    assert release.release_status == "STABLE"
    assert release.active_model_version == run.candidate_model_version
    assert release.active_index_name == run.candidate_index_name
    assert release.previous_model_version == "baseline-v1"
    assert release.previous_index_name == "active-index-v1"
    from src.infra.db.snapshot_registry import ModelSnapshotStore
    previous_stable = await ModelSnapshotStore(session).get_rollback_target()
    assert previous_stable is not None
    assert previous_stable.model_version == "baseline-v1"
    assert previous_stable.index_name == "active-index-v1"
    assert release.candidate_model_version is None
    assert release.candidate_index_name is None


async def test_rollback_smoke_restores_snapshot_and_reenters_project(
    session: AsyncSession,
) -> None:
    trace_id = uuid4()
    switched_at = _FixedClock().now()
    release = ModelReleaseModel(
        release_status="STABLE",
        active_model_version="candidate-v1",
        active_index_name="candidate-index-v1",
        previous_model_version="baseline-v1",
        previous_index_name="active-index-v1",
        switched_at=switched_at,
    )
    project, video, chunk = await _seed_problem_model_project(session)
    session.add(release)
    await session.flush()

    from src.infra.db.snapshot_registry import ModelSnapshotStore

    snapshot_store = ModelSnapshotStore(session)
    await snapshot_store.record_cutover(
        model_version="baseline-v1", index_name="active-index-v1", captured_at=switched_at
    )
    await snapshot_store.record_cutover(
        model_version="candidate-v1", index_name="candidate-index-v1", captured_at=switched_at
    )
    await session.flush()

    rollback = await RollbackTransitionManager(
        release_store=ModelReleaseStore(session),
        project_store=ProjectRollbackStore(session),
        target_readiness=AlwaysReadyRollbackTarget(),
        index_restore=ImmediateIndexRestore(),
        clock=_FixedClock(),
    ).handle_request(
        {
            "message_type": "ROLLBACK_REQUEST",
            "payload_version": "v1",
            "trace_id": trace_id,
            "attempt": 1,
            "issued_at": switched_at,
            "expected_active_model_version": "candidate-v1",
            "expected_switched_at": switched_at,
        }
    )

    assert rollback.status == "restored"
    assert rollback.affected_project_count == 1
    assert release.release_status == "STABLE"
    assert release.active_model_version == "baseline-v1"
    assert release.active_index_name == "active-index-v1"
    assert release.previous_model_version is None
    assert release.previous_index_name is None
    assert project.search_serving_state == "ROLLBACK_EXCLUDED"

    sink = _RecordingReembeddingSink()
    recovery = await RollbackRecoveryService(
        project_store=ProjectRollbackStore(session),
        reembedding_sink=sink,
    ).dispatch_restored_reembedding(
        active_model_version="baseline-v1",
        active_index_name="active-index-v1",
    )

    assert recovery.requested_video_count == 1
    assert sink.calls == [
        {
            "video_id": video.id,
            "target_model_version": "baseline-v1",
            "target_index_name": "active-index-v1",
        }
    ]

    chunk.embedding_model_version = "baseline-v1"
    session.add(
        VectorIndexEntryModel(
            index_name="active-index-v1",
            chunk_id=chunk.id,
            user_id=video.user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="baseline-v1",
            created_at=_FixedClock().now(),
        )
    )
    await session.flush()
    reopened_count = await ProjectRollbackStore(session).reenter_restored_projects(
        active_model_version="baseline-v1",
        active_index_name="active-index-v1",
        updated_at=_FixedClock().now(),
    )

    assert reopened_count == 1
    assert project.search_serving_state == "SERVABLE"


async def _open_candidate_release(
    session: AsyncSession,
    tmp_path,
) -> tuple[
    InMemoryArtifactStore,
    DatasetArtifactRefs,
    MLPipelineRunModel,
    ModelEvaluationModel,
    ModelReleaseModel,
]:
    trace_id = uuid4()
    store = _smoke_store(trace_id)
    session.add(
        ModelReleaseModel(
            release_status="STABLE",
            active_model_version="baseline-v1",
            active_index_name="active-index-v1",
        )
    )
    await session.flush()

    from src.infra.db.snapshot_registry import ModelSnapshotStore

    # Seed the baseline generation as ACTIVE so the candidate cutover can
    # demote it to PREVIOUS_STABLE when recording the new generation.
    await ModelSnapshotStore(session).record_cutover(
        model_version="baseline-v1",
        index_name="active-index-v1",
        captured_at=datetime(2026, 5, 13, 9, 0, tzinfo=UTC),
    )

    dataset_refs = await _materialize_dataset(store, tmp_path, eligible_for_training=True)

    training_result = await TrainingRequestHandler(
        manifest_selector=DatasetManifestSelector(
            store,
            policy=DatasetEligibilityPolicy(
                min_training_group_count=1,
                min_negative_count=1,
            ),
        ),
        dataset_artifact_prefix="feedback/datasets",
        run_slot_store=DbRunSlotStore(session, model_version_prefix=MODEL_VERSION_PREFIX),
        model_release_store=ModelReleaseStore(session),
        run_executor=_run_executor(session=session, store=store, tmp_path=tmp_path),
        workspace_dir=tmp_path,
        clock=_FixedClock(),
    ).handle(
        {
            "message_type": "TRAINING_REQUEST",
            "payload_version": "v1",
            "trace_id": trace_id,
            "attempt": 1,
            "issued_at": datetime(2026, 5, 13, 9, 1, tzinfo=UTC),
        }
    )
    run = await session.scalar(select(MLPipelineRunModel))
    evaluation = await session.scalar(select(ModelEvaluationModel))
    release = await ModelReleaseStore(session).get_current()

    assert training_result.created is True
    assert training_result.executed is True
    assert run is not None
    assert evaluation is not None
    assert release is not None
    return store, dataset_refs, run, evaluation, release


def _smoke_store(trace_id: UUID) -> InMemoryArtifactStore:
    return InMemoryArtifactStore(
        {
            "feedback/raw/schema_version=1/ingest_date=2026-05-13/hour=09/events.jsonl": (
                json.dumps(
                    {
                        "event_id": "evt-smoke-1",
                        "trace_id": str(trace_id),
                        "req_id": "req-smoke-1",
                        "user_id": "user-smoke-1",
                        "project_id": "project-smoke-1",
                        "query_text": "semantic search",
                        "rating": "LIKE",
                        "topk_ids": ["chunk_pos", "chunk_neg"],
                        "used_ids": ["chunk_pos"],
                        "active_model_version": "baseline-v1",
                        "active_index_name": "active-index-v1",
                        "response_snapshot_ref": "snapshot:req-smoke-1",
                        "created_at": "2026-05-13T08:55:00Z",
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode(),
            "eval/smoke.json": (
                b'{"queries":[{"query_text":"semantic search","relevant_chunk_ids":["chunk_pos"]}],'
                b'"corpus":[{"chunk_id":"chunk_pos","chunk_text":"semantic search ranking"},'
                b'{"chunk_id":"chunk_neg","chunk_text":"unrelated cooking recipe"}]}'
            ),
            "models/baseline-v1/config.json": b"{}",
        }
    )


async def _materialize_dataset(
    store: InMemoryArtifactStore,
    tmp_path,
    *,
    eligible_for_training: bool = False,
) -> DatasetArtifactRefs:
    materializer = None
    if eligible_for_training:
        materializer = DatasetMaterializer(
            eligibility_policy=DatasetEligibilityPolicy(
                min_training_group_count=1,
                min_negative_count=1,
            )
        )
    return await DatasetBatchService(
        artifact_store=store,
        chunk_text_snapshot=_ChunkTextSnapshot(),
        materializer=materializer,
        clock=_FixedClock(),
    ).materialize_latest(
        raw_feedback_log_prefix="feedback/raw/",
        dataset_artifact_prefix="feedback/datasets",
        workspace_dir=tmp_path,
        source_window_start=datetime(2026, 5, 13, 0, 0, tzinfo=UTC),
        source_window_end=datetime(2026, 5, 14, 0, 0, tzinfo=UTC),
    )


def _run_executor(
    *,
    session: AsyncSession,
    store: InMemoryArtifactStore,
    tmp_path,
) -> RunFlowExecutor:
    return RunFlowExecutor(
        session=session,
        dataset_artifact_uri_prefix="gs://test-bucket/feedback/datasets",
        training_runner=LocalTrainingRunner(
            artifact_store=store,
            model_artifact_prefix="feedback/models",
            serving_model_artifact_prefix="models",
            base_model_name="local-smoke",
            embedding_dimension=384,
        ),
        evaluator=OfflineEvaluationService(
            dataset_loader=EvaluationDatasetLoader(store),
            evaluator=OfflineEvaluator(LocalEmbeddingSearchBackend(dimensions=384)),
            model_artifact_loader=LocalScoringModelArtifactLoader(
                artifact_store=store,
                model_artifact_prefix="feedback/models",
            ),
            workspace_dir=tmp_path,
            clock=_FixedClock(),
        ),
        evaluation_recorder=EvaluationRecorder(
            session=session,
            detail_writer=EvaluationDetailArtifactWriter(
                artifact_store=store,
                evaluation_artifact_prefix="feedback/evaluations",
            ),
            workspace_dir=tmp_path,
            clock=_FixedClock(),
        ),
        handoff_sink=CandidateReleaseHandoffSink(_serving_manager(session)),
        evaluation_dataset_ref="gs://test-bucket/eval/smoke.json",
        training_config_ref="configs/training/smoke.yaml",
        training_config_hash="sha256:smoke",
        workspace_dir=tmp_path,
        clock=_FixedClock(),
    )


def _serving_manager(session: AsyncSession) -> ServingTransitionManager:
    return ServingTransitionManager(
        run_store=MLPipelineRunStore(session),
        release_store=ModelReleaseStore(session),
        clock=_FixedClock(),
    )


async def _seed_problem_model_project(
    session: AsyncSession,
) -> tuple[ProjectModel, VideoModel, ChunkModel]:
    now = _FixedClock().now()
    user_id = uuid4()
    project = ProjectModel(user_id=user_id, title="Rollback Project", created_at=now, updated_at=now)
    session.add(project)
    await session.flush()
    video = VideoModel(
        user_id=user_id,
        project_id=project.id,
        title="Rollback Video",
        status="READY",
        updated_at=now,
    )
    session.add(video)
    await session.flush()
    chunk = ChunkModel(video_id=video.id, text="problem model chunk", embedding_model_version="candidate-v1")
    session.add(chunk)
    await session.flush()
    session.add(
        VectorIndexEntryModel(
            index_name="candidate-index-v1",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="candidate-v1",
            created_at=now,
        )
    )
    await session.flush()
    return project, video, chunk
