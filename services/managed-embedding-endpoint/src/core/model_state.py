import threading


class ModelState:
    """여러 thread에서 안전하게 ready model version 목록을 관리한다."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready_model_versions: list[str] = []

    @property
    def ready(self) -> bool:
        with self._lock:
            return bool(self._ready_model_versions)

    @property
    def model_version(self) -> str:
        with self._lock:
            return self._ready_model_versions[0] if self._ready_model_versions else ""

    @property
    def ready_model_versions(self) -> list[str]:
        with self._lock:
            return list(self._ready_model_versions)

    def mark_ready(self, model_version: str) -> None:
        with self._lock:
            if model_version not in self._ready_model_versions:
                self._ready_model_versions.append(model_version)

    def clear_ready_version(self, model_version: str | None = None) -> None:
        with self._lock:
            if model_version is None:
                self._ready_model_versions = []
                return
            self._ready_model_versions = [
                version
                for version in self._ready_model_versions
                if version != model_version
            ]

    def is_ready(self, model_version: str) -> bool:
        with self._lock:
            return model_version in self._ready_model_versions

    def replace_ready_versions(self, model_versions: list[str]) -> None:
        with self._lock:
            seen: set[str] = set()
            self._ready_model_versions = []
            for version in model_versions:
                if version in seen:
                    continue
                self._ready_model_versions.append(version)
                seen.add(version)
