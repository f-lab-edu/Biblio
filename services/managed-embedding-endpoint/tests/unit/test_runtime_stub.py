import pytest

from src.infra.runtime import EmbeddingRuntime


class _StubRuntime:
    """Stub runtime returning deterministic embeddings based on text length."""

    EMBEDDING_DIM = 4

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] * self.EMBEDDING_DIM for t in texts]


def _assert_is_embedding_runtime(obj: object) -> None:
    """Structural check: obj satisfies EmbeddingRuntime protocol."""
    runtime: EmbeddingRuntime = obj  # type: ignore[assignment]
    assert callable(getattr(runtime, "encode", None))


class TestRuntimeShape:
    def test_returns_list_of_list_of_float(self):
        runtime = _StubRuntime()
        result = runtime.encode(["hello"])

        assert isinstance(result, list)
        assert isinstance(result[0], list)
        assert all(isinstance(v, float) for v in result[0])

    def test_output_length_matches_input(self):
        runtime = _StubRuntime()
        texts = ["a", "bb", "ccc"]

        result = runtime.encode(texts)

        assert len(result) == len(texts)

    def test_each_embedding_has_expected_dimension(self):
        runtime = _StubRuntime()
        result = runtime.encode(["test"])

        assert len(result[0]) == _StubRuntime.EMBEDDING_DIM


class TestRuntimeOrderPreservation:
    def test_embeddings_preserve_input_order(self):
        runtime = _StubRuntime()
        texts = ["a", "bb", "ccc"]

        result = runtime.encode(texts)

        expected = [
            pytest.approx([1.0] * _StubRuntime.EMBEDDING_DIM),
            pytest.approx([2.0] * _StubRuntime.EMBEDDING_DIM),
            pytest.approx([3.0] * _StubRuntime.EMBEDDING_DIM),
        ]
        assert result == expected

    def test_empty_input_returns_empty_list(self):
        runtime = _StubRuntime()

        result = runtime.encode([])

        assert result == []


class TestProtocolCompliance:
    def test_stub_satisfies_protocol(self):
        _assert_is_embedding_runtime(_StubRuntime())
