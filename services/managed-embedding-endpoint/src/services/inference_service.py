import time

from src.core.model_state import ModelState
from src.core.settings import Settings
from src.infra.runtime import EmbeddingRuntime
from src.middlewares.error_handler import (
    InvalidArgumentError,
    PayloadTooLargeError,
    ServiceUnavailableError,
)
from src.observability.logging import error, info


class InferenceService:
    """Orchestrates guardrail checks, admission control, and embedding inference."""

    def __init__(
        self,
        settings: Settings,
        model_state: ModelState,
        runtime: EmbeddingRuntime,
    ) -> None:
        self._settings = settings
        self._model_state = model_state
        self._runtime = runtime

    def embed(
        self,
        texts: list[str],
        payload_size: int,
        trace_id: str | None = None,
    ) -> list[list[float]]:
        self._check_guardrails(texts, payload_size)
        self._check_readiness()
        start = time.monotonic()
        result = self._call_runtime(texts, trace_id)
        elapsed_ms = (time.monotonic() - start) * 1000
        info(
            "embed.success",
            text_count=len(texts),
            payload_size=payload_size,
            duration_ms=round(elapsed_ms, 1),
            model_version=self._model_state.model_version,
            trace_id=trace_id,
        )
        return result

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

    def _check_readiness(self) -> None:
        if not self._model_state.ready:
            raise ServiceUnavailableError("Model is not ready.")

    def _call_runtime(
        self,
        texts: list[str],
        trace_id: str | None,
    ) -> list[list[float]]:
        try:
            embeddings = self._runtime.encode(texts)
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
