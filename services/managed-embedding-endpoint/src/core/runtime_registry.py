import threading
from collections.abc import Mapping

from src.infra.runtime import EmbeddingRuntime


class RuntimeRegistry:
    """Thread-safe runtime map with atomic whole-registry replacement."""

    def __init__(
        self,
        runtimes: Mapping[str, EmbeddingRuntime] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._runtimes = dict(runtimes or {})

    def get(self, model_version: str) -> EmbeddingRuntime | None:
        with self._lock:
            return self._runtimes.get(model_version)

    def snapshot(self) -> dict[str, EmbeddingRuntime]:
        with self._lock:
            return dict(self._runtimes)

    def replace(
        self,
        runtimes: Mapping[str, EmbeddingRuntime],
    ) -> dict[str, EmbeddingRuntime]:
        with self._lock:
            previous = self._runtimes
            self._runtimes = dict(runtimes)
            return previous
