from pathlib import Path

import pytest

from src.core.model_state import ModelState
from src.infra.model_loader import ModelLoader
from src.infra.runtime import EmbeddingRuntime


class _StubRuntime:
    """Minimal stub satisfying EmbeddingRuntime protocol."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 3 for _ in texts]


class _SuccessLoader(ModelLoader):
    """Loader that always succeeds."""

    def load(self, artifact_path: str) -> EmbeddingRuntime:
        version = self._resolve_version(artifact_path)
        self._model_state.mark_ready(version)
        return _StubRuntime()


class _FailLoader(ModelLoader):
    """Loader that always fails (simulates missing path)."""

    def load(self, artifact_path: str) -> EmbeddingRuntime:
        raise FileNotFoundError(f"artifact not found: {artifact_path}")


class TestLoadSuccess:
    def test_model_state_becomes_ready(self, tmp_path: Path):
        state = ModelState()
        loader = _SuccessLoader(state)

        loader.load(str(tmp_path / "models/bge-m3"))

        assert state.ready is True

    def test_model_version_is_resolved_artifact_path(self, tmp_path: Path):
        state = ModelState()
        loader = _SuccessLoader(state)
        artifact = str(tmp_path / "models/bge-m3")

        loader.load(artifact)

        expected_version = str(Path(artifact).resolve())
        assert state.model_version == expected_version

    def test_returns_embedding_runtime(self, tmp_path: Path):
        state = ModelState()
        loader = _SuccessLoader(state)

        runtime = loader.load(str(tmp_path / "models/bge-m3"))

        assert hasattr(runtime, "encode")

    def test_model_version_uses_remote_model_id_when_path_missing(self):
        state = ModelState()
        loader = _SuccessLoader(state)

        loader.load("BAAI/bge-m3")

        assert state.model_version == "BAAI/bge-m3"


class TestLoadFailure:
    def test_model_state_stays_not_ready(self):
        state = ModelState()
        loader = _FailLoader(state)

        with pytest.raises(FileNotFoundError):
            loader.load("/nonexistent/path")

        assert state.ready is False
        assert state.model_version == ""

    def test_previously_ready_state_unchanged_on_second_load_failure(self):
        state = ModelState()
        state.mark_ready("old-version")

        loader = _FailLoader(state)
        with pytest.raises(FileNotFoundError):
            loader.load("/nonexistent/path")

        # V1 only loads at startup, so this reload scenario is not in SPEC.
        # Defensive behavior: a failed load must not corrupt existing ready state.
        assert state.ready is True
        assert state.model_version == "old-version"
