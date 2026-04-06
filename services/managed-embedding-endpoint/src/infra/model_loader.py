from abc import ABC, abstractmethod
from pathlib import Path

from src.core.model_state import ModelState
from src.infra.runtime import EmbeddingRuntime


class ModelLoader(ABC):
    def __init__(self, model_state: ModelState) -> None:
        self._model_state = model_state

    @abstractmethod
    def load(self, artifact_path: str) -> EmbeddingRuntime:
        """Load model from *artifact_path*, return an EmbeddingRuntime.

        On success the loader MUST call ``model_state.mark_ready(model_version)``
        where *model_version* is derived from the resolved artifact path.

        On failure the loader MUST leave model_state not-ready and raise.
        """
        ...

    def _resolve_version(self, artifact_path: str) -> str:
        path = Path(artifact_path)
        if path.exists():
            return str(path.resolve())
        return artifact_path
