from __future__ import annotations


class BgeEmbeddingRuntime:
    """EmbeddingRuntime backed by BGEM3FlagModel (dense vectors only)."""

    def __init__(self, model: object, *, max_length: int = 512) -> None:
        self._model = model
        self._max_length = max_length

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        result = self._model.encode(  # type: ignore[union-attr]
            texts,
            max_length=self._max_length,
        )
        dense_vecs = result["dense_vecs"]
        #  BGE-M3가 만든 임베딩 벡터를 NumPy 배열 같은 전용 자료형에서 일반 Python list로 바꿈
        return [vec.tolist() if hasattr(vec, "tolist") else list(vec) for vec in dense_vecs]
