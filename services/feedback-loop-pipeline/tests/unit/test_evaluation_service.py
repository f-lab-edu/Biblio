from datetime import UTC, datetime

import pytest

from src.evaluation.dataset_loader import EvaluationDatasetLoader
from src.evaluation.evaluator import OfflineEvaluator
from src.evaluation.local_search import LocalEmbeddingSearchBackend
from src.evaluation.model_artifacts import LocalScoringModelArtifactLoader
from src.evaluation.service import OfflineEvaluationService
from src.infra.storage.inmemory import InMemoryArtifactStore


class _FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 5, 12, 10, 0, tzinfo=UTC)


async def test_offline_evaluation_service_loads_model_artifacts_for_ranking(tmp_path) -> None:
    store = InMemoryArtifactStore(
        {
            "eval/eval-v1.json": (
                b'{"queries":[{"query_text":"alpha beta","relevant_chunk_ids":["chunk-b"]}],'
                b'"corpus":[{"chunk_id":"chunk-a","chunk_text":"alpha"},'
                b'{"chunk_id":"chunk-b","chunk_text":"beta"}]}'
            ),
            "models/baseline-v1/scoring_artifact.json": (
                b'{"artifact_format":"local-weighted-token-v1","model_version":"baseline-v1",'
                b'"term_weights":{"alpha":10.0,"beta":1.0}}'
            ),
            "models/candidate-v1/scoring_artifact.json": (
                b'{"artifact_format":"local-weighted-token-v1","model_version":"candidate-v1",'
                b'"term_weights":{"alpha":1.0,"beta":10.0}}'
            ),
        }
    )

    result = await OfflineEvaluationService(
        dataset_loader=EvaluationDatasetLoader(store),
        evaluator=OfflineEvaluator(LocalEmbeddingSearchBackend(dimensions=256)),
        model_artifact_loader=LocalScoringModelArtifactLoader(
            artifact_store=store,
            model_artifact_prefix="models",
        ),
        workspace_dir=tmp_path,
        clock=_FixedClock(),
    ).evaluate(
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
        evaluation_dataset_ref="gs://test-bucket/eval/eval-v1.json",
    )

    assert result.overall_decision == "PASS"
    assert result.quality_metrics["mrr_at_5"] == pytest.approx(1.0)
    assert result.details[0].baseline_ranked_chunk_ids == ["chunk-a", "chunk-b"]
    assert result.details[0].candidate_ranked_chunk_ids == ["chunk-b", "chunk-a"]
