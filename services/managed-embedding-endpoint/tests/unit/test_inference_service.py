from collections.abc import Callable

import pytest

from src.core.runtime_registry import RuntimeRegistry
from src.core.settings import Settings
from src.middlewares.error_handler import (
    InvalidArgumentError,
    PayloadTooLargeError,
    ServiceUnavailableError,
)
from src.services.inference_service import InferenceService

EMBEDDING_DIM = 4


class _StubRuntime:
    """Deterministic runtime: embedding[i] = [float(len(text))] * dim."""

    def __init__(self, dim: int = EMBEDDING_DIM, offset: float = 0.0) -> None:
        self._dim = dim
        self._offset = offset
        self.call_log: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.call_log.append(texts)
        return [[float(len(t)) + self._offset] * self._dim for t in texts]


class _ErrorRuntime:
    """Runtime that always raises."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("model crashed")


class _WrongLengthRuntime:
    """Returns fewer embeddings than inputs."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 2.0]] * (len(texts) - 1) if len(texts) > 1 else []


class _BadTypeRuntime:
    """Returns embeddings containing non-numeric values."""

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [["not", "a", "float"]] * len(texts)  # type: ignore[list-item]


class TestConstruction:
    def test_uses_injected_runtime_registry(
        self,
        settings_factory: Callable[..., Settings],
    ):
        runtime = _StubRuntime()
        service = InferenceService(
            settings=settings_factory(),
            runtime_registry=RuntimeRegistry({"test-model": runtime}),
        )

        result = service.embed(["hello"], payload_size=10, model_version="test-model")

        assert result[0] == pytest.approx([5.0] * EMBEDDING_DIM)


class TestGuardrails:
    def test_texts_count_exceeds_max(
        self,
        inference_service_factory: Callable[..., InferenceService],
        settings_factory: Callable[..., Settings],
    ):
        service = inference_service_factory(
            settings=settings_factory(max_texts_per_request=2)
        )

        with pytest.raises(InvalidArgumentError, match="Too many texts"):
            service.embed(["a", "b", "c"], payload_size=10, model_version="test-model")

    def test_individual_text_length_exceeds_max(
        self,
        inference_service_factory: Callable[..., InferenceService],
        settings_factory: Callable[..., Settings],
    ):
        service = inference_service_factory(
            settings=settings_factory(max_text_length_chars=5)
        )

        with pytest.raises(InvalidArgumentError, match="index 0 is 10 chars"):
            service.embed(["a" * 10], payload_size=10, model_version="test-model")

    def test_payload_size_exceeds_max(
        self,
        inference_service_factory: Callable[..., InferenceService],
        settings_factory: Callable[..., Settings],
    ):
        service = inference_service_factory(
            settings=settings_factory(max_payload_bytes=50)
        )

        with pytest.raises(PayloadTooLargeError, match="exceeds maximum 50"):
            service.embed(["hello"], payload_size=100, model_version="test-model")

    def test_all_within_limits_passes(
        self,
        inference_service_factory: Callable[..., InferenceService],
        settings_factory: Callable[..., Settings],
    ):
        service = inference_service_factory(
            settings=settings_factory(
                max_texts_per_request=10,
                max_text_length_chars=100,
                max_payload_bytes=10000,
            )
        )

        result = service.embed(["hello", "world"], payload_size=30, model_version="test-model")

        assert len(result) == 2


class TestReadinessCheck:
    def test_model_not_ready_raises(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory(runtime_registry=RuntimeRegistry())

        with pytest.raises(ServiceUnavailableError, match="not ready"):
            service.embed(["hello"], payload_size=10, model_version="test-model")

    def test_model_ready_passes(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory()

        result = service.embed(["hello"], payload_size=10, model_version="test-model")

        assert len(result) == 1


class TestEmbedSuccess:
    def test_single_text_returns_correct_embedding(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        runtime = _StubRuntime()
        service = inference_service_factory(runtime=runtime)

        result = service.embed(["hello"], payload_size=10, model_version="test-model")

        assert len(result) == 1
        assert result[0] == pytest.approx([5.0] * EMBEDDING_DIM)

    def test_batch_texts_correct_length_and_order(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        runtime = _StubRuntime()
        service = inference_service_factory(runtime=runtime)

        texts = ["a", "bb", "ccc"]
        result = service.embed(texts, payload_size=20, model_version="test-model")

        assert len(result) == 3
        assert result[0] == pytest.approx([1.0] * EMBEDDING_DIM)
        assert result[1] == pytest.approx([2.0] * EMBEDDING_DIM)
        assert result[2] == pytest.approx([3.0] * EMBEDDING_DIM)

    def test_runtime_called_with_exact_input(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        runtime = _StubRuntime()
        service = inference_service_factory(runtime=runtime)

        texts = ["foo", "bar"]
        service.embed(texts, payload_size=10, model_version="test-model")

        assert runtime.call_log == [["foo", "bar"]]


class TestResponseValidation:
    def test_wrong_length_raises(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory(runtime=_WrongLengthRuntime())

        with pytest.raises(ServiceUnavailableError, match="2 embeddings for 3 texts"):
            service.embed(["a", "b", "c"], payload_size=10, model_version="test-model")

    def test_empty_when_shouldnt_raises(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory(runtime=_WrongLengthRuntime())

        with pytest.raises(ServiceUnavailableError, match="0 embeddings for 1 texts"):
            service.embed(["a"], payload_size=5, model_version="test-model")

    def test_runtime_exception_raises_unavailable(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory(runtime=_ErrorRuntime())

        with pytest.raises(ServiceUnavailableError, match="runtime error"):
            service.embed(["hello"], payload_size=10, model_version="test-model")

    def test_non_numeric_embedding_raises(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory(runtime=_BadTypeRuntime())

        with pytest.raises(ServiceUnavailableError, match="not a list of numeric"):
            service.embed(["hello"], payload_size=10, model_version="test-model")


class TestLogging:
    def test_success_log_includes_trace_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory()
        captured: dict[str, object] = {}

        def _fake_info(message: str, **fields: object) -> None:
            captured["message"] = message
            captured["fields"] = fields

        monkeypatch.setattr("src.services.inference_service.info", _fake_info)

        service.embed(
            ["hello"],
            payload_size=10,
            model_version="test-model",
            trace_id="trace-123",
        )

        assert captured["message"] == "embed.success"
        assert captured["fields"]["trace_id"] == "trace-123"
        assert captured["fields"]["model_version"] == "test-model"

    def test_runtime_error_log_includes_trace_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory(runtime=_ErrorRuntime())
        captured: dict[str, object] = {}

        def _fake_error(message: str, **fields: object) -> None:
            captured["message"] = message
            captured["fields"] = fields

        monkeypatch.setattr("src.services.inference_service.error", _fake_error)

        with pytest.raises(ServiceUnavailableError, match="runtime error"):
            service.embed(
                ["hello"],
                payload_size=10,
                model_version="test-model",
                trace_id="trace-456",
            )

        assert captured["message"] == "runtime.encode failed"
        assert captured["fields"]["trace_id"] == "trace-456"


class TestModelVersionRouting:
    def test_unknown_model_version_raises(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory()

        with pytest.raises(ServiceUnavailableError, match="not ready"):
            service.embed(["hello"], payload_size=10, model_version="missing-model")

    def test_routes_to_requested_model_runtime(
        self,
        inference_service_factory: Callable[..., InferenceService],
    ):
        service = inference_service_factory(
            runtimes={
                "fake-20260526T143000KST": _StubRuntime(offset=0.0),
                "fake-20260526T144000KST": _StubRuntime(offset=10.0),
            },
        )

        result = service.embed(
            ["abcd"],
            payload_size=10,
            model_version="fake-20260526T144000KST",
        )

        assert result[0] == pytest.approx([14.0] * EMBEDDING_DIM)
