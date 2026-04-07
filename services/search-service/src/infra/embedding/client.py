"""Embedding HTTP client for Managed Embedding Endpoint.

Search Service sends a single normalized query text and receives
one embedding vector. Retry policy: timeout/503 up to EMBEDDING_MAX_RETRIES
with 200ms exponential backoff.
"""

from dataclasses import dataclass

import httpx

from src.common.retry import RetryableError, retry_with_backoff
from src.middlewares.error_handler import ServiceUnavailableError


@dataclass(slots=True)
class EmbeddingResult:
    embedding: list[float]


class EmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_sec: int = 2,
        max_retries: int = 1,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = timeout_sec
        self._max_retries = max_retries
        self._client = client or httpx.AsyncClient()

    async def embed_query(self, query: str, *, trace_id: str) -> EmbeddingResult:
        """Embed a single normalized query text.

        Returns the first embedding vector from the response.
        Raises ServiceUnavailableError on final failure.
        """

        async def _attempt() -> EmbeddingResult:
            try:
                response = await self._client.post(
                    f"{self._base_url}/embed",
                    json={"texts": [query]},
                    headers={"X-Trace-Id": trace_id},
                    timeout=self._timeout_sec,
                )
            except httpx.TimeoutException:
                raise RetryableError(
                    ServiceUnavailableError("Embedding endpoint timed out")
                )

            if response.status_code == 503:
                raise RetryableError(
                    ServiceUnavailableError("Embedding endpoint unavailable")
                )

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise ServiceUnavailableError(
                    f"Embedding endpoint returned {exc.response.status_code}"
                ) from exc

            payload = response.json()
            embeddings = payload.get("embeddings")

            # Validate response shape per SPEC §2.1 / §4.1
            if not embeddings or len(embeddings) != 1:
                raise ServiceUnavailableError(
                    "Embedding response shape mismatch: "
                    f"expected 1 embedding, got {len(embeddings) if embeddings else 0}"
                )

            vector = embeddings[0]
            if not isinstance(vector, list) or not all(
                isinstance(v, (int, float)) for v in vector
            ):
                raise ServiceUnavailableError(
                    "Embedding response contains non-numeric vector"
                )

            return EmbeddingResult(embedding=vector)

        return await retry_with_backoff(
            _attempt, max_retries=self._max_retries
        )

    async def aclose(self) -> None:
        await self._client.aclose()
