"""Tests for EmbeddingClient per SPEC §2.1 Managed Embedding Endpoint."""

import httpx
import pytest

from src.infra.embedding.client import EmbeddingClient
from src.middlewares.error_handler import ServiceUnavailableError

TRACE_ID = "00000000-0000-0000-0000-000000000001"


def _make_client(handler: httpx.MockTransport) -> EmbeddingClient:
    return EmbeddingClient(
        base_url="https://test-embed",
        timeout_sec=2,
        max_retries=1,
        client=httpx.AsyncClient(transport=handler),
    )


class TestEmbedQuery:
    async def test_successful_embedding(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

        client = _make_client(httpx.MockTransport(handler))
        result = await client.embed_query(
            "test query",
            trace_id=TRACE_ID,
            model_version="embedding-v1",
        )
        assert result.embedding == [0.1, 0.2, 0.3]

    async def test_sends_request_headers(self) -> None:
        captured_headers: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(request.headers))
            return httpx.Response(200, json={"embeddings": [[0.1]]})

        client = _make_client(httpx.MockTransport(handler))
        await client.embed_query(
            "test",
            trace_id=TRACE_ID,
            model_version="embedding-v1",
        )
        assert captured_headers["x-trace-id"] == TRACE_ID
        assert captured_headers["x-embedding-workload"] == "search"

    async def test_sends_single_text(self) -> None:
        captured_body: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json
            captured_body.update(json.loads(request.content))
            return httpx.Response(200, json={"embeddings": [[0.1]]})

        client = _make_client(httpx.MockTransport(handler))
        await client.embed_query(
            "my query",
            trace_id=TRACE_ID,
            model_version="embedding-v1",
        )
        assert captured_body == {
            "texts": ["my query"],
            "model_version": "embedding-v1",
        }

    async def test_empty_embeddings_raises_503(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": []})

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(ServiceUnavailableError):
            await client.embed_query(
                "test",
                trace_id=TRACE_ID,
                model_version="embedding-v1",
            )

    async def test_length_mismatch_raises_503(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": [[0.1], [0.2]]})

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(ServiceUnavailableError):
            await client.embed_query(
                "test",
                trace_id=TRACE_ID,
                model_version="embedding-v1",
            )

    async def test_non_numeric_vector_raises_503(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": [["a", "b"]]})

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(ServiceUnavailableError):
            await client.embed_query(
                "test",
                trace_id=TRACE_ID,
                model_version="embedding-v1",
            )

    async def test_503_retries_then_raises(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(503)

        client = _make_client(httpx.MockTransport(handler))
        with pytest.raises(ServiceUnavailableError):
            await client.embed_query(
                "test",
                trace_id=TRACE_ID,
                model_version="embedding-v1",
            )
        assert call_count == 2  # 1 initial + 1 retry

    async def test_503_then_success(self) -> None:
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(503)
            return httpx.Response(200, json={"embeddings": [[0.5]]})

        client = _make_client(httpx.MockTransport(handler))
        result = await client.embed_query(
            "test",
            trace_id=TRACE_ID,
            model_version="embedding-v1",
        )
        assert result.embedding == [0.5]
        assert call_count == 2
