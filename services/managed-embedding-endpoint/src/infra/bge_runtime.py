from __future__ import annotations


class BgeEmbeddingRuntime:
    """EmbeddingRuntime backed by BGEM3FlagModel (dense vectors only)."""

    def __init__(self, model: object) -> None:
        self._model = model

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self._model.encode(texts)  # type: ignore[union-attr]
        dense_vecs = result["dense_vecs"]
        return [vec.tolist() if hasattr(vec, "tolist") else list(vec) for vec in dense_vecs]
