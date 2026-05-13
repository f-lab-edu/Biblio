from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.evaluation.artifacts import EvaluationDetailArtifactWriter
from src.evaluation.evaluator import EvaluationDetail, EvaluationResult
from src.evaluation.recorder import EvaluationRecorder
from src.infra.db.models import Base, MLPipelineRunModel, ModelEvaluationModel
from src.infra.storage.inmemory import InMemoryArtifactStore
from src.run_control.consumer import RunFlowExecutor
from src.training.manifest import ModelArtifactManifest
from src.training.runner import LocalTrainingRunner, TrainingInput


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 11, 10, 0, tzinfo=UTC)


class _Evaluator:
    def __init__(self, decision: str) -> None:
        self.decision = decision

    async def evaluate(
        self,
        *,
        baseline_model_version: str,
        candidate_model_version: str,
        evaluation_dataset_ref: str,
    ) -> EvaluationResult:
        # Async to satisfy the EvaluatorPort test double contract.
        metrics = (
            {"recall_at_5": 1.0, "mrr_at_5": 1.0, "ndcg_at_5": 1.0}
            if self.decision == "PASS"
            else {"recall_at_5": 0.0, "mrr_at_5": 0.0, "ndcg_at_5": 0.0}
        )
        return EvaluationResult(
            candidate_model_version=candidate_model_version,
            baseline_model_version=baseline_model_version,
            evaluation_dataset_ref=evaluation_dataset_ref,
            sample_count=1,
            quality_metrics=metrics,
            pass_criteria={"rule": "candidate_metrics_gte_baseline"},
            overall_decision=self.decision,
            fail_reason=None if self.decision == "PASS" else "candidate metrics did not meet baseline",
            details=[
                EvaluationDetail(
                    query_text="semantic search",
                    relevant_chunk_ids=["chunk-1"],
                    baseline_ranked_chunk_ids=["chunk-1"],
                    candidate_ranked_chunk_ids=["chunk-1"] if self.decision == "PASS" else [],
                    baseline_metrics={"recall_at_5": 1.0, "mrr_at_5": 1.0, "ndcg_at_5": 1.0},
                    candidate_metrics=metrics,
                )
            ],
            evaluated_at=datetime(2026, 5, 11, 10, 30, tzinfo=UTC),
        )


class _FailingTrainingRunner:
    async def train(self, training_input: TrainingInput, *, workspace_dir):
        # Async to satisfy the TrainingRunner test double contract.
        raise RuntimeError("training crashed")


class _HandoffSink:
    def __init__(self) -> None:
        self.calls: list[dict[str, UUID]] = []

    async def ready_for_release(self, *, run_id: UUID, trace_id: UUID) -> None:
        # Async to satisfy the HandoffSinkPort test double contract.
        self.calls.append({"run_id": run_id, "trace_id": trace_id})


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


async def _seed_running_run(session: AsyncSession) -> MLPipelineRunModel:
    run = MLPipelineRunModel(
        id=uuid4(),
        status="RUNNING",
        dataset_version="dataset-v1",
        baseline_model_version="baseline-from-run",
        candidate_model_version="candidate-v1",
        created_at=datetime(2026, 5, 11, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 5, 11, 9, 0, tzinfo=UTC),
    )
    session.add(run)
    await session.flush()
    return run


def _executor(
    *,
    session: AsyncSession,
    artifact_store: InMemoryArtifactStore,
    evaluator,
    handoff_sink: _HandoffSink,
    tmp_path,
):
    return RunFlowExecutor(
        session=session,
        dataset_artifact_uri_prefix="feedback/datasets",
        training_runner=LocalTrainingRunner(
            artifact_store=artifact_store,
            model_artifact_prefix="feedback/models",
            base_model_name="local-smoke",
            embedding_dimension=384,
        ),
        evaluator=evaluator,
        evaluation_recorder=EvaluationRecorder(
            session=session,
            detail_writer=EvaluationDetailArtifactWriter(
                artifact_store=artifact_store,
                evaluation_artifact_prefix="feedback/evaluations",
            ),
            workspace_dir=tmp_path,
            clock=_FixedClock(),
        ),
        handoff_sink=handoff_sink,
        evaluation_dataset_ref="gs://test-bucket/eval/eval-v1.json",
        training_config_ref="configs/training/smoke.yaml",
        training_config_hash="sha256:abc123",
        workspace_dir=tmp_path,
        clock=_FixedClock(),
    )


async def test_passed_run_persists_evaluation_artifacts_and_hands_off(
    session: AsyncSession,
    tmp_path,
) -> None:
    run = await _seed_running_run(session)
    trace_id = uuid4()
    artifact_store = InMemoryArtifactStore()
    handoff = _HandoffSink()

    await _executor(
        session=session,
        artifact_store=artifact_store,
        evaluator=_Evaluator("PASS"),
        handoff_sink=handoff,
        tmp_path=tmp_path,
    ).execute(
        run_id=run.id,
        trace_id=trace_id,
    )

    refreshed_run = await session.get(MLPipelineRunModel, run.id)
    evaluation = await session.get(ModelEvaluationModel, refreshed_run.evaluation_id)
    manifest = ModelArtifactManifest.from_json(
        artifact_store.objects["feedback/models/candidate-v1/model_manifest.json"]
    )
    detail_paths = [
        path
        for path in artifact_store.objects
        if path.startswith("feedback/evaluations/") and path.endswith("/details.jsonl")
    ]

    assert refreshed_run.status == "READY_FOR_RELEASE"
    assert evaluation is not None
    assert evaluation.overall_decision == "PASS"
    assert evaluation.quality_metrics["recall_at_5"] == pytest.approx(1.0)
    assert manifest.candidate_model_version == "candidate-v1"
    assert manifest.baseline_model_version == "baseline-from-run"
    assert manifest.dataset_version == "dataset-v1"
    assert detail_paths
    assert b'"query_text":"semantic search"' in artifact_store.objects[detail_paths[0]]
    assert handoff.calls == [{"run_id": run.id, "trace_id": trace_id}]


async def test_failed_evaluation_marks_run_failed_without_handoff(
    session: AsyncSession,
    tmp_path,
) -> None:
    run = await _seed_running_run(session)
    artifact_store = InMemoryArtifactStore()
    handoff = _HandoffSink()

    await _executor(
        session=session,
        artifact_store=artifact_store,
        evaluator=_Evaluator("FAIL"),
        handoff_sink=handoff,
        tmp_path=tmp_path,
    ).execute(
        run_id=run.id,
        trace_id=uuid4(),
    )

    refreshed_run = await session.get(MLPipelineRunModel, run.id)
    evaluation = await session.get(ModelEvaluationModel, refreshed_run.evaluation_id)

    assert refreshed_run.status == "FAILED"
    assert refreshed_run.failed_stage == "평가"
    assert refreshed_run.failure_type == "FAIL"
    assert evaluation is not None
    assert evaluation.overall_decision == "FAIL"
    assert handoff.calls == []


async def test_training_error_marks_run_failed_without_evaluation_or_handoff(
    session: AsyncSession,
    tmp_path,
) -> None:
    run = await _seed_running_run(session)
    artifact_store = InMemoryArtifactStore()
    handoff = _HandoffSink()

    await RunFlowExecutor(
        session=session,
        dataset_artifact_uri_prefix="feedback/datasets",
        training_runner=_FailingTrainingRunner(),
        evaluator=_Evaluator("PASS"),
        evaluation_recorder=EvaluationRecorder(
            session=session,
            detail_writer=EvaluationDetailArtifactWriter(
                artifact_store=artifact_store,
                evaluation_artifact_prefix="feedback/evaluations",
            ),
            workspace_dir=tmp_path,
            clock=_FixedClock(),
        ),
        handoff_sink=handoff,
        evaluation_dataset_ref="gs://test-bucket/eval/eval-v1.json",
        training_config_ref="configs/training/smoke.yaml",
        training_config_hash="sha256:abc123",
        workspace_dir=tmp_path,
        clock=_FixedClock(),
    ).execute(
        run_id=run.id,
        trace_id=uuid4(),
    )

    refreshed_run = await session.get(MLPipelineRunModel, run.id)
    evaluation_count = await session.scalar(select(func.count()).select_from(ModelEvaluationModel))

    assert refreshed_run.status == "FAILED"
    assert refreshed_run.failed_stage == "학습"
    assert refreshed_run.failure_type == "ERROR"
    assert "training crashed" in refreshed_run.failure_reason
    assert evaluation_count == 0
    assert handoff.calls == []
