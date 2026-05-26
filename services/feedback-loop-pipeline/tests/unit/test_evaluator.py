from datetime import UTC, datetime

import pytest

from src.evaluation.evaluator import (
    EvaluationCorpusRow,
    EvaluationDataset,
    EvaluationQuery,
    OfflineEvaluator,
    StaticSearchBackend,
)
from src.evaluation.local_search import LocalEmbeddingSearchBackend
from src.evaluation.model_artifacts import LocalScoringModelArtifact


def test_evaluator_computes_recall_mrr_ndcg_and_pass_decision() -> None:
    dataset = EvaluationDataset(
        evaluation_dataset_ref="gs://bucket/eval/eval-v1.json",
        queries=[
            EvaluationQuery(
                query_text="semantic search",
                relevant_chunk_ids=["chunk-1", "chunk-3"],
            )
        ],
        corpus=[
            EvaluationCorpusRow(chunk_id="chunk-1", chunk_text="semantic search guide"),
            EvaluationCorpusRow(chunk_id="chunk-2", chunk_text="irrelevant"),
            EvaluationCorpusRow(chunk_id="chunk-3", chunk_text="search examples"),
        ],
    )
    backend = StaticSearchBackend(
        {
            ("baseline-v1", "semantic search"): ["chunk-2", "chunk-1", "chunk-3"],
            ("candidate-v1", "semantic search"): ["chunk-1", "chunk-3", "chunk-2"],
        }
    )

    result = OfflineEvaluator(backend).evaluate(
        dataset,
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
        evaluated_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
    )

    assert result.sample_count == 1
    assert result.overall_decision == "PASS"
    assert result.quality_metrics["recall_at_5"] == pytest.approx(1.0)
    assert result.quality_metrics["mrr_at_5"] == pytest.approx(1.0)
    assert result.quality_metrics["ndcg_at_5"] == pytest.approx(1.0)
    assert result.details[0].candidate_ranked_chunk_ids[:2] == ["chunk-1", "chunk-3"]


def test_evaluator_fails_when_candidate_metric_is_worse_than_baseline() -> None:
    dataset = EvaluationDataset(
        evaluation_dataset_ref="gs://bucket/eval/eval-v1.json",
        queries=[
            EvaluationQuery(query_text="semantic search", relevant_chunk_ids=["chunk-1"]),
        ],
        corpus=[
            EvaluationCorpusRow(chunk_id="chunk-1", chunk_text="semantic search guide"),
            EvaluationCorpusRow(chunk_id="chunk-2", chunk_text="irrelevant"),
        ],
    )
    backend = StaticSearchBackend(
        {
            ("baseline-v1", "semantic search"): ["chunk-1", "chunk-2"],
            ("candidate-v1", "semantic search"): ["chunk-2"],
        }
    )

    result = OfflineEvaluator(backend).evaluate(
        dataset,
        baseline_model_version="baseline-v1",
        candidate_model_version="candidate-v1",
        evaluated_at=datetime(2026, 5, 3, 12, 0, tzinfo=UTC),
    )

    assert result.overall_decision == "FAIL"
    assert result.fail_reason == "candidate metrics did not meet baseline"
    assert result.quality_metrics["recall_at_5"] == pytest.approx(0.0)


def test_local_search_backend_uses_model_specific_scoring_artifacts() -> None:
    corpus = [
        EvaluationCorpusRow(chunk_id="chunk-a", chunk_text="alpha"),
        EvaluationCorpusRow(chunk_id="chunk-b", chunk_text="beta"),
    ]
    backend = LocalEmbeddingSearchBackend(
        dimensions=256,
        model_artifacts={
            "baseline-v1": LocalScoringModelArtifact(term_weights={"alpha": 10.0, "beta": 1.0}),
            "candidate-v1": LocalScoringModelArtifact(term_weights={"alpha": 1.0, "beta": 10.0}),
        },
    )

    assert backend.search(
        model_version="baseline-v1",
        query_text="alpha beta",
        corpus=corpus,
        top_k=2,
    ) == ["chunk-a", "chunk-b"]
    assert backend.search(
        model_version="candidate-v1",
        query_text="alpha beta",
        corpus=corpus,
        top_k=2,
    ) == ["chunk-b", "chunk-a"]
