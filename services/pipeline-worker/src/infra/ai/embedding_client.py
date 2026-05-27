import asyncio
from dataclasses import dataclass

import httpx

from src.infra.ai.google_stt_adapter import ExternalAIAdapterError


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
        model_version: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries
        self._model_version = model_version
        self._client = client or httpx.AsyncClient()

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
        if not texts:
            raise ExternalAIAdapterError(
                code="INVALID_REQUEST",
                message="texts must not be empty",
                trace_id=trace_id,
                provider="embedding-endpoint",
                retryable=False,
            )

        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    f"{self._base_url}/embed",
                    json={
                        "texts": texts,
                        "model_version": model_version or self._model_version,
                    },
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
                return EmbeddingBatchResult(
                    embeddings=embeddings,
                    model_version=model_version or self._model_version,
                )
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

    async def aclose(self) -> None:
        await self._client.aclose()
