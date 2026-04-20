from typing import Protocol


class EmbeddingRuntime(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]:
        """Return dense embeddings preserving input order: embeddings[i] ↔ texts[i]."""
        ...
