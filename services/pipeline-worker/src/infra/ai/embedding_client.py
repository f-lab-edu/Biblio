import asyncio
import random
import time
from dataclasses import dataclass

import httpx
from loguru import logger

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
from src.infra.ai.retry_policy import (
    JitterCallable,
    SleepCallable,
    exponential_backoff_with_jitter,
)


def _log_request_event(
    *,
    level: str,
    event: str,
    trace_id: str,
    model_version: str,
    text_count: int,
    attempt: int,
    duration_ms: float,
    status_code: int | str,
    error_code: str,
    retry_delay_seconds: float,
) -> None:
    logger.bind(trace_id=trace_id).log(
        level,
        "event={} model_version={} text_count={} attempt={} duration_ms={:.1f} "
        "status_code={} error_code={} retry_delay_seconds={:.3f}",
        event,
        model_version,
        text_count,
        attempt,
        duration_ms,
        status_code,
        error_code,
        retry_delay_seconds,
    )


@dataclass(slots=True)
class EmbeddingBatchResult:
    embeddings: list[list[float]]
    model_version: str


@dataclass(frozen=True, slots=True)
class _EmbeddingAttemptContext:
    trace_id: str
    model_version: str
    text_count: int
    attempt: int
    started_at: float


def _log_attempt(
    context: _EmbeddingAttemptContext,
    *,
    level: str,
    event: str,
    status_code: int | str,
    error_code: str = "-",
    retry_delay_seconds: float = 0.0,
) -> None:
    _log_request_event(
        level=level,
        event=event,
        trace_id=context.trace_id,
        model_version=context.model_version,
        text_count=context.text_count,
        attempt=context.attempt,
        duration_ms=(time.monotonic() - context.started_at) * 1000,
        status_code=status_code,
        error_code=error_code,
        retry_delay_seconds=retry_delay_seconds,
    )


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: int,
        max_retries: int,
        model_version: str,
        client: httpx.AsyncClient | None = None,
        sleep: SleepCallable = asyncio.sleep,
        jitter: JitterCallable = random.random,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries
        self._model_version = model_version
        self._client = client or httpx.AsyncClient()
        self._sleep = sleep
        self._jitter = jitter

    async def get_ready_model_versions(self, trace_id: str) -> list[str]:
        response = await self._client.get(
            f"{self._base_url}/health",
            headers={"X-Trace-Id": trace_id},
            timeout=self._timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        ready_model_versions = payload.get("ready_model_versions")
        if not isinstance(ready_model_versions, list) or not ready_model_versions:
            raise ExternalAIAdapterError(
                code="INTERNAL_ERROR",
                message="Embedding ready_model_versions missing from health response",
                trace_id=trace_id,
                provider="embedding-endpoint",
                retryable=False,
            )
        return [str(version) for version in ready_model_versions]

    async def get_model_version(self, trace_id: str) -> str:
        ready_model_versions = await self.get_ready_model_versions(trace_id)
        if self._model_version in ready_model_versions:
            return self._model_version
        if len(ready_model_versions) == 1:
            return ready_model_versions[0]
        raise ExternalAIAdapterError(
            code="INTERNAL_ERROR",
            message="Configured embedding model_version is not ready",
            trace_id=trace_id,
            provider="embedding-endpoint",
            retryable=False,
        )

    async def embed_texts(
        self,
        texts: list[str],
        *,
        trace_id: str,
        model_version: str | None = None,
    ) -> EmbeddingBatchResult:
        self._validate_texts(texts, trace_id)
        requested_model_version = model_version or self._model_version
        for attempt_index in range(self._max_retries + 1):
            result = await self._attempt_embedding_batch(
                texts=texts,
                trace_id=trace_id,
                model_version=requested_model_version,
                attempt_index=attempt_index,
            )
            if result is not None:
                return result
        raise RuntimeError("Embedding retry loop exited unexpectedly")

    @staticmethod
    def _validate_texts(texts: list[str], trace_id: str) -> None:
        if texts:
            return
        raise ExternalAIAdapterError(
            code="INVALID_REQUEST",
            message="texts must not be empty",
            trace_id=trace_id,
            provider="embedding-endpoint",
            retryable=False,
        )

    async def _attempt_embedding_batch(
        self,
        *,
        texts: list[str],
        trace_id: str,
        model_version: str,
        attempt_index: int,
    ) -> EmbeddingBatchResult | None:
        context = _EmbeddingAttemptContext(
            trace_id=trace_id,
            model_version=model_version,
            text_count=len(texts),
            attempt=attempt_index + 1,
            started_at=time.monotonic(),
        )
        try:
            result, status_code = await self._post_embedding_batch(texts, trace_id, model_version)
        except httpx.TimeoutException as exc:
            _log_attempt(
                context,
                level="ERROR",
                event="embedding.request.timeout",
                status_code="-",
                error_code="TIMEOUT",
            )
            raise ExternalAIAdapterError(
                code="TIMEOUT",
                message="Embedding endpoint timed out",
                trace_id=trace_id,
                provider="embedding-endpoint",
                retryable=False,
                attempt_count=context.attempt,
            ) from exc
        except ExternalAIAdapterError as exc:
            return await self._handle_adapter_error(
                exc=exc,
                context=context,
                attempt_index=attempt_index,
            )
        except httpx.HTTPStatusError as exc:
            _log_attempt(
                context,
                level="ERROR",
                event="embedding.request.failed",
                status_code=exc.response.status_code,
                error_code="HTTP_STATUS_ERROR",
            )
            raise
        except Exception as exc:
            _log_attempt(
                context,
                level="ERROR",
                event="embedding.request.failed",
                status_code="-",
                error_code=type(exc).__name__,
            )
            raise
        _log_attempt(
            context,
            level="INFO",
            event="embedding.request.success",
            status_code=status_code,
        )
        return result

    async def _post_embedding_batch(
        self,
        texts: list[str],
        trace_id: str,
        model_version: str,
    ) -> tuple[EmbeddingBatchResult, int]:
        response = await self._client.post(
            f"{self._base_url}/embed",
            json={"texts": texts, "model_version": model_version},
            headers={
                "X-Trace-Id": trace_id,
                "X-Embedding-Workload": "video_preprocess",
            },
            timeout=self._timeout_sec,
        )
        if response.status_code == 503:
            raise ExternalAIAdapterError(
                code="UNAVAILABLE",
                message="Embedding endpoint unavailable",
                trace_id=trace_id,
                provider="embedding-endpoint",
                retryable=True,
            )
        response.raise_for_status()
        embeddings = response.json()["embeddings"]
        if len(embeddings) != len(texts):
            raise ExternalAIAdapterError(
                code="INTERNAL_ERROR",
                message="Embedding count mismatch",
                trace_id=trace_id,
                provider="embedding-endpoint",
                retryable=False,
            )
        return EmbeddingBatchResult(embeddings=embeddings, model_version=model_version), response.status_code

    async def _handle_adapter_error(
        self,
        *,
        exc: ExternalAIAdapterError,
        context: _EmbeddingAttemptContext,
        attempt_index: int,
    ) -> None:
        exc.attempt_count = context.attempt
        status_code = 503 if exc.code == "UNAVAILABLE" else 200
        if exc.code != "UNAVAILABLE" or attempt_index >= self._max_retries:
            _log_attempt(
                context,
                level="ERROR",
                event="embedding.request.failed",
                status_code=status_code,
                error_code=exc.code,
            )
            raise exc
        delay_seconds = exponential_backoff_with_jitter(attempt_index, self._jitter())
        _log_attempt(
            context,
            level="WARNING",
            event="embedding.request.retry",
            status_code=status_code,
            error_code=exc.code,
            retry_delay_seconds=delay_seconds,
        )
        await self._sleep(delay_seconds)
        return None

    async def aclose(self) -> None:
        await self._client.aclose()
