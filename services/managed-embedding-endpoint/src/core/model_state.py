import threading


class ModelState:
    """Thread-safe container for model readiness and version tracking."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = False
        self._model_version: str = ""

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    @property
    def model_version(self) -> str:
        with self._lock:
            return self._model_version

    def mark_ready(self, model_version: str) -> None:
        with self._lock:
            self._ready = True
            self._model_version = model_version

    def mark_not_ready(self) -> None:
        with self._lock:
            self._ready = False
            self._model_version = ""
