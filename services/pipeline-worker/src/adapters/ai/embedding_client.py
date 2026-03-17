from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from adapters.ai.google_stt_adapter import ExternalAIAdapterError


@dataclass(slots=True)
class EmbeddingBatchResult:
    embeddings: list[list[float]]
    model_version: str


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: int,
        max_retries: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries
        self._client = client or httpx.AsyncClient()

    async def get_model_version(self, trace_id: str) -> str:
        response = await self._client.get(
            f"{self._base_url}/health",
            headers={"X-Trace-Id": trace_id},
            timeout=self._timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        model_version = payload.get("model_version")
        if not model_version:
            raise ExternalAIAdapterError(
                code="INTERNAL_ERROR",
                message="Embedding model_version missing from health response",
                trace_id=trace_id,
                provider="embedding-endpoint",
                retryable=False,
            )
        return str(model_version)

    async def embed_texts(self, texts: list[str], *, trace_id: str) -> EmbeddingBatchResult:
        if not texts:
            raise ExternalAIAdapterError(
                code="INVALID_REQUEST",
                message="texts must not be empty",
                trace_id=trace_id,
                provider="embedding-endpoint",
                retryable=False,
            )

        model_version = await self.get_model_version(trace_id)
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}/embed",
                    json={"texts": texts},
                    headers={"X-Trace-Id": trace_id},
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
                return EmbeddingBatchResult(embeddings=embeddings, model_version=model_version)
            except httpx.TimeoutException:
                last_error = ExternalAIAdapterError(
                    code="TIMEOUT",
                    message="Embedding endpoint timed out",
                    trace_id=trace_id,
                    provider="embedding-endpoint",
                    retryable=True,
                )
            except ExternalAIAdapterError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            if attempt >= self._max_retries:
                assert last_error is not None
                raise last_error
            await asyncio.sleep(0)

        assert last_error is not None
        raise last_error
