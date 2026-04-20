"""Reciprocal Rank Fusion (RRF) merger.

RRF_score(d) = Σ 1 / (k + rank(d))

Merges FTS and ANN candidates into a single ranked list.
"""

from dataclasses import dataclass
from uuid import UUID

from src.infra.db.search_repository import ANNCandidate, FTSCandidate


@dataclass(slots=True)
class RRFCandidate:
    chunk_id: UUID
    score: float


def rrf_merge(
    fts: list[FTSCandidate],
    ann: list[ANNCandidate],
    *,
    k: int,
    top_k: int,
) -> list[RRFCandidate]:
    """Merge FTS and ANN candidates using RRF scoring.

    Returns candidates sorted by RRF score descending, limited to top_k.
    """
    scores: dict[UUID, float] = {}

    for candidate in fts:
        scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (k + candidate.rank)

    for candidate in ann:
        scores[candidate.chunk_id] = scores.get(candidate.chunk_id, 0.0) + 1.0 / (k + candidate.rank)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    return [
        RRFCandidate(chunk_id=chunk_id, score=score)
        for chunk_id, score in ranked[:top_k]
    ]
