from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from src.evaluation.model_artifacts import LocalScoringModelArtifact


TOP_K = 5


@dataclass(frozen=True)
class EvaluationCorpusRow:
    chunk_id: str
    chunk_text: str


@dataclass(frozen=True)
class EvaluationQuery:
    query_text: str
    relevant_chunk_ids: list[str]


@dataclass(frozen=True)
class EvaluationDataset:
    evaluation_dataset_ref: str
    queries: list[EvaluationQuery]
    corpus: list[EvaluationCorpusRow]


@dataclass(frozen=True)
class EvaluationDetail:
    query_text: str
    relevant_chunk_ids: list[str]
    baseline_ranked_chunk_ids: list[str]
    candidate_ranked_chunk_ids: list[str]
    baseline_metrics: dict[str, float]
    candidate_metrics: dict[str, float]


@dataclass(frozen=True)
class EvaluationResult:
    candidate_model_version: str
    baseline_model_version: str
    evaluation_dataset_ref: str
    sample_count: int
    quality_metrics: dict[str, float]
    pass_criteria: dict[str, object]
    overall_decision: str
    fail_reason: str | None
    details: list[EvaluationDetail]
    evaluated_at: datetime


class SearchBackend(Protocol):
    def search(
        self,
        *,
        model_version: str,
        query_text: str,
        corpus: list[EvaluationCorpusRow],
        top_k: int,
    ) -> list[str]: ...


class StaticSearchBackend:
    def __init__(self, results_by_model_query: dict[tuple[str, str], list[str]]) -> None:
        self._results_by_model_query = results_by_model_query

    def search(
        self,
        *,
        model_version: str,
        query_text: str,
        corpus: list[EvaluationCorpusRow],
        top_k: int,
    ) -> list[str]:
        return self._results_by_model_query.get((model_version, query_text), [])[:top_k]


class OfflineEvaluator:
    def __init__(self, search_backend: SearchBackend) -> None:
        self._search_backend = search_backend

    def register_model_artifact(self, model_version: str, artifact: LocalScoringModelArtifact) -> None:
        register = getattr(self._search_backend, "register_model_artifact", None)
        if register is not None:
            register(model_version, artifact)

    def evaluate(
        self,
        dataset: EvaluationDataset,
        *,
        baseline_model_version: str,
        candidate_model_version: str,
        evaluated_at: datetime,
    ) -> EvaluationResult:
        details = [
            self._evaluate_query(
                query,
                dataset.corpus,
                baseline_model_version=baseline_model_version,
                candidate_model_version=candidate_model_version,
            )
            for query in dataset.queries
        ]
        baseline_metrics = _average_metrics([detail.baseline_metrics for detail in details])
        candidate_metrics = _average_metrics([detail.candidate_metrics for detail in details])
        overall_decision = "PASS" if _candidate_meets_baseline(candidate_metrics, baseline_metrics) else "FAIL"
        return EvaluationResult(
            candidate_model_version=candidate_model_version,
            baseline_model_version=baseline_model_version,
            evaluation_dataset_ref=dataset.evaluation_dataset_ref,
            sample_count=len(dataset.queries),
            quality_metrics=candidate_metrics,
            pass_criteria={
                "rule": "candidate_metrics_gte_baseline",
                "baseline_metrics": baseline_metrics,
            },
            overall_decision=overall_decision,
            fail_reason=None if overall_decision == "PASS" else "candidate metrics did not meet baseline",
            details=details,
            evaluated_at=evaluated_at,
        )

    def _evaluate_query(
        self,
        query: EvaluationQuery,
        corpus: list[EvaluationCorpusRow],
        *,
        baseline_model_version: str,
        candidate_model_version: str,
    ) -> EvaluationDetail:
        baseline_ranked = self._search_backend.search(
            model_version=baseline_model_version,
            query_text=query.query_text,
            corpus=corpus,
            top_k=TOP_K,
        )
        candidate_ranked = self._search_backend.search(
            model_version=candidate_model_version,
            query_text=query.query_text,
            corpus=corpus,
            top_k=TOP_K,
        )
        return EvaluationDetail(
            query_text=query.query_text,
            relevant_chunk_ids=query.relevant_chunk_ids,
            baseline_ranked_chunk_ids=baseline_ranked,
            candidate_ranked_chunk_ids=candidate_ranked,
            baseline_metrics=_metrics_at_k(baseline_ranked, query.relevant_chunk_ids),
            candidate_metrics=_metrics_at_k(candidate_ranked, query.relevant_chunk_ids),
        )


def _metrics_at_k(ranked_ids: list[str], relevant_ids: list[str]) -> dict[str, float]:
    relevant_set = set(relevant_ids)
    ranked_at_k = ranked_ids[:TOP_K]
    if not relevant_set:
        return {"recall_at_5": 0.0, "mrr_at_5": 0.0, "ndcg_at_5": 0.0}
    hits = [1 if chunk_id in relevant_set else 0 for chunk_id in ranked_at_k]
    recall = sum(hits) / len(relevant_set)
    reciprocal_rank = next((1 / rank for rank, hit in enumerate(hits, start=1) if hit), 0.0)
    dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
    ideal_hits = [1] * min(len(relevant_set), TOP_K)
    ideal_dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(ideal_hits))
    ndcg = dcg / ideal_dcg if ideal_dcg > 0 else 0.0
    return {"recall_at_5": recall, "mrr_at_5": reciprocal_rank, "ndcg_at_5": ndcg}


def _average_metrics(metric_rows: list[dict[str, float]]) -> dict[str, float]:
    if not metric_rows:
        return {"recall_at_5": 0.0, "mrr_at_5": 0.0, "ndcg_at_5": 0.0}
    return {
        key: sum(metrics[key] for metrics in metric_rows) / len(metric_rows)
        for key in ("recall_at_5", "mrr_at_5", "ndcg_at_5")
    }


def _candidate_meets_baseline(
    candidate_metrics: dict[str, float],
    baseline_metrics: dict[str, float],
) -> bool:
    return all(
        candidate_metrics[key] >= baseline_metrics[key]
        for key in ("recall_at_5", "mrr_at_5", "ndcg_at_5")
    )
