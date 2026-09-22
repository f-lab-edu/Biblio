from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.core.model_state import ModelState
from src.infra.bge_runtime import BgeEmbeddingRuntime
from src.infra.model_loader import ModelLoader
from src.infra.runtime import EmbeddingRuntime


def _default_model_factory(artifact_path: str, **kwargs: Any) -> Any:
    from FlagEmbedding import BGEM3FlagModel  # noqa: N813

    return BGEM3FlagModel(artifact_path, **kwargs)


class BgeModelLoader(ModelLoader):
    """ModelLoader that creates a BGEM3FlagModel and wraps it in BgeEmbeddingRuntime."""

    def __init__(
        self,
        model_state: ModelState,
        model_cache_dir: str = "",
        embedding_max_length: int = 512,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(model_state)
        self._model_cache_dir = model_cache_dir
        self._embedding_max_length = embedding_max_length
        self._model_factory = model_factory or _default_model_factory

    def load(self, artifact_path: str) -> EmbeddingRuntime:
        path = Path(artifact_path)
        if self._is_missing_local_path(path, artifact_path):
            raise FileNotFoundError(f"Model artifact path does not exist: {artifact_path}")

        resolved_artifact = str(path.resolve()) if path.exists() else artifact_path

        kwargs: dict[str, object] = {"use_fp16": True}
        if self._model_cache_dir:
            kwargs["cache_dir"] = self._model_cache_dir

        model = self._model_factory(resolved_artifact, **kwargs)

        model_version = self._resolve_version(artifact_path)
        self._model_state.mark_ready(model_version)
        return BgeEmbeddingRuntime(model, max_length=self._embedding_max_length)

    @staticmethod
    def _is_missing_local_path(path: Path, artifact_path: str) -> bool:
        # Absolute and dot-prefixed paths are explicit local-path intent.
        return not path.exists() and (path.is_absolute() or artifact_path.startswith("."))
