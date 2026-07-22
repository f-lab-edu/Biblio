import time

from src.core.runtime_registry import RuntimeRegistry
from src.core.settings import Settings
from src.infra.runtime import EmbeddingRuntime
from src.middlewares.error_handler import (
    InvalidArgumentError,
    PayloadTooLargeError,
    ServiceUnavailableError,
)
from src.observability.logging import error, info

# Embedding 요청을 지정된 model runtime으로 라우팅하고 입력/응답을 검증한다.
class InferenceService:

    def __init__(
        self,
        settings: Settings,
        runtime_registry: RuntimeRegistry,
    ) -> None:
        self._settings = settings
        self._runtime_registry = runtime_registry

    def embed(
        self,
        texts: list[str],
        payload_size: int,
        model_version: str,
        trace_id: str | None = None,
    ) -> list[list[float]]:
        self._check_guardrails(texts, payload_size)
        runtime = self._runtime_for(model_version)
        start = time.monotonic()
        result = self._call_runtime(runtime, texts, trace_id)
        elapsed_ms = (time.monotonic() - start) * 1000
        info(
            "embed.success",
            text_count=len(texts),
            payload_size=payload_size,
            duration_ms=round(elapsed_ms, 1),
            model_version=model_version,
            trace_id=trace_id,
        )
        return result

    def validate_request(
        self,
        texts: list[str],
        payload_size: int,
        model_version: str,
    ) -> None:
        """Reject invalid work before it consumes bounded waiting capacity."""
        self._check_guardrails(texts, payload_size)
        self._runtime_for(model_version)

    def _check_guardrails(self, texts: list[str], payload_size: int) -> None:
        max_texts = self._settings.max_texts_per_request
        if len(texts) > max_texts:
            raise InvalidArgumentError(
                f"Too many texts: {len(texts)} exceeds maximum {max_texts}."
            )

        max_chars = self._settings.max_text_length_chars
        for i, text in enumerate(texts):
            if len(text) > max_chars:
                raise InvalidArgumentError(
                    f"Text at index {i} is {len(text)} chars, exceeds maximum {max_chars}."
                )

        max_bytes = self._settings.max_payload_bytes
        if payload_size > max_bytes:
            raise PayloadTooLargeError(
                f"Payload size {payload_size} bytes exceeds maximum {max_bytes}."
            )

    def _runtime_for(self, model_version: str) -> EmbeddingRuntime:
        runtime = self._runtime_registry.get(model_version)
        if runtime is None:
            raise ServiceUnavailableError(
                f"Model version is not ready: {model_version}."
            )
        return runtime

    def _call_runtime(
        self,
        runtime: EmbeddingRuntime,
        texts: list[str],
        trace_id: str | None,
    ) -> list[list[float]]:
        try:
            embeddings = runtime.encode(texts)
        except Exception as exc:
            error("runtime.encode failed", error=str(exc), trace_id=trace_id)
            raise ServiceUnavailableError("Embedding runtime error.") from exc

        self._validate_response(texts, embeddings)
        return embeddings

    def _validate_response(
        self, texts: list[str], embeddings: list[list[float]]
    ) -> None:
        if len(embeddings) != len(texts):
            raise ServiceUnavailableError(
                f"Runtime returned {len(embeddings)} embeddings for {len(texts)} texts."
            )

        for i, emb in enumerate(embeddings):
            if not isinstance(emb, list) or not all(
                isinstance(v, (int, float)) for v in emb
            ):
                raise ServiceUnavailableError(
                    f"Embedding at index {i} is not a list of numeric values."
                )
