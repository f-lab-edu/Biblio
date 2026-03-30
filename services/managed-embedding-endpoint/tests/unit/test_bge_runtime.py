from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.core.model_state import ModelState
from src.infra.bge_runtime import BgeEmbeddingRuntime

try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


def _make_mock_model(dense_vecs: list) -> MagicMock:
    model = MagicMock()
    model.encode.return_value = {"dense_vecs": dense_vecs}
    return model


class TestBgeEmbeddingRuntimeEncode:
    def test_calls_model_encode_and_extracts_dense_vecs(self):
        vecs = [[0.1, 0.2], [0.3, 0.4]]
        model = _make_mock_model(vecs)
        runtime = BgeEmbeddingRuntime(model)

        result = runtime.encode(["hello", "world"])

        model.encode.assert_called_once_with(["hello", "world"])
        assert result == [pytest.approx([0.1, 0.2]), pytest.approx([0.3, 0.4])]

    @pytest.mark.skipif(not _HAS_NUMPY, reason="numpy not available")
    def test_converts_numpy_arrays_to_plain_lists(self):
        vecs = [np.array([0.1, 0.2], dtype=np.float32), np.array([0.3, 0.4], dtype=np.float32)]
        model = _make_mock_model(vecs)
        runtime = BgeEmbeddingRuntime(model)

        result = runtime.encode(["a", "b"])

        for embedding in result:
            assert isinstance(embedding, list)
            for val in embedding:
                assert isinstance(val, float)
        assert result == [pytest.approx([0.1, 0.2]), pytest.approx([0.3, 0.4])]

    def test_preserves_input_order(self):
        vecs = [[1.0], [2.0], [3.0]]
        model = _make_mock_model(vecs)
        runtime = BgeEmbeddingRuntime(model)

        result = runtime.encode(["first", "second", "third"])

        assert result == [pytest.approx([1.0]), pytest.approx([2.0]), pytest.approx([3.0])]

    def test_empty_input_returns_empty_list(self):
        model = MagicMock()
        runtime = BgeEmbeddingRuntime(model)

        result = runtime.encode([])

        assert result == []
        model.encode.assert_not_called()


class TestBgeModelLoaderSuccess:
    def test_load_marks_state_ready_and_returns_runtime(self, tmp_path: Path):
        from src.infra.bge_loader import BgeModelLoader

        artifact_dir = tmp_path / "model"
        artifact_dir.mkdir()

        state = ModelState()
        fake_model = MagicMock()
        factory = MagicMock(return_value=fake_model)
        loader = BgeModelLoader(state, model_factory=factory)

        runtime = loader.load(str(artifact_dir))

        factory.assert_called_once_with(str(artifact_dir.resolve()), use_fp16=True)
        assert state.ready is True
        assert state.model_version == str(artifact_dir.resolve())
        assert isinstance(runtime, BgeEmbeddingRuntime)

    def test_load_passes_cache_dir_when_set(self, tmp_path: Path):
        from src.infra.bge_loader import BgeModelLoader

        artifact_dir = tmp_path / "model"
        artifact_dir.mkdir()

        state = ModelState()
        fake_model = MagicMock()
        factory = MagicMock(return_value=fake_model)
        loader = BgeModelLoader(state, model_cache_dir="/tmp/cache", model_factory=factory)

        loader.load(str(artifact_dir))

        factory.assert_called_once_with(
            str(artifact_dir.resolve()), use_fp16=True, cache_dir="/tmp/cache"
        )


class TestBgeModelLoaderFailure:
    def test_load_raises_on_missing_path(self):
        from src.infra.bge_loader import BgeModelLoader

        state = ModelState()
        loader = BgeModelLoader(state)

        with pytest.raises(FileNotFoundError):
            loader.load("/nonexistent/path/to/model")

        assert state.ready is False
        assert state.model_version == ""
