from __future__ import annotations

import hashlib
import math
from typing import Mapping

from src.evaluation.evaluator import EvaluationCorpusRow
from src.evaluation.model_artifacts import LocalScoringModelArtifact
from src.utils.text import tokens


class LocalEmbeddingSearchBackend:
    def __init__(
        self,
        *,
        dimensions: int,
        model_artifacts: Mapping[str, LocalScoringModelArtifact] | None = None,
    ) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions
        self._model_artifacts = dict(model_artifacts or {})

    def register_model_artifact(self, model_version: str, artifact: LocalScoringModelArtifact) -> None:
        self._model_artifacts[model_version] = artifact

    def search(
        self,
        *,
        model_version: str,
        query_text: str,
        corpus: list[EvaluationCorpusRow],
        top_k: int,
    ) -> list[str]:
        artifact = self._model_artifacts.get(model_version, LocalScoringModelArtifact(term_weights={}))
        query_vector = self._embed(query_text, artifact)
        scored_rows = [
            (self._cosine_similarity(query_vector, self._embed(row.chunk_text, artifact)), row.chunk_id)
            for row in corpus
        ]
        return [
            chunk_id
            for _, chunk_id in sorted(scored_rows, key=lambda scored: (-scored[0], scored[1]))[:top_k]
        ]

    def _embed(self, text: str, artifact: LocalScoringModelArtifact) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], byteorder="big") % self._dimensions
            vector[index] += artifact.term_weights.get(token, 1.0)
        return vector

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm <= 0.0 or right_norm <= 0.0:
            return 0.0
        dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
        return dot_product / (left_norm * right_norm)
