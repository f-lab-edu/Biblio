import json
from urllib.error import URLError
from uuid import uuid4

import pytest

from src.release.model_reload import (
    ManagedEmbeddingModelReloadClient,
    ManagedEmbeddingModelReloadFanout,
    ModelReloadError,
)


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.mark.asyncio
async def test_reload_client_posts_reload_models_and_returns_ready_versions() -> None:
    calls = []

    def urlopen(request, timeout):
        calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )
        return _Response({"ready_model_versions": ["model-v1", "model-v2"]})

    trace_id = uuid4()
    result = await ManagedEmbeddingModelReloadClient(
        base_url="https://embedding.local",
        timeout_sec=2.5,
        urlopen_func=urlopen,
    ).reload(trace_id=trace_id)

    assert result.ready_model_versions == frozenset({"model-v1", "model-v2"})
    assert calls == [
        {
            "url": "https://embedding.local/internal/reload-models",
            "method": "POST",
            "body": {"trace_id": str(trace_id)},
            "timeout": pytest.approx(2.5),
        }
    ]


@pytest.mark.asyncio
async def test_reload_client_raises_on_endpoint_failure() -> None:
    def urlopen(request, timeout):
        raise URLError("endpoint down")

    with pytest.raises(ModelReloadError):
        await ManagedEmbeddingModelReloadClient(
            base_url="https://embedding.local",
            timeout_sec=2.5,
            urlopen_func=urlopen,
        ).reload(trace_id=uuid4())


@pytest.mark.asyncio
async def test_reload_fanout_calls_both_endpoints_and_returns_intersection() -> None:
    calls: list[str] = []

    def batch_urlopen(request, timeout):
        calls.append(request.full_url)
        return _Response({"ready_model_versions": ["active-v1", "candidate-v2"]})

    def search_urlopen(request, timeout):
        calls.append(request.full_url)
        return _Response({"ready_model_versions": ["candidate-v2"]})

    client = ManagedEmbeddingModelReloadFanout(
        batch_client=ManagedEmbeddingModelReloadClient(
            base_url="https://embedding-batch.local",
            urlopen_func=batch_urlopen,
        ),
        search_client=ManagedEmbeddingModelReloadClient(
            base_url="https://embedding-search.local",
            urlopen_func=search_urlopen,
        ),
    )

    result = await client.reload(trace_id=uuid4())

    assert result.ready_model_versions == frozenset({"candidate-v2"})
    assert set(calls) == {
        "https://embedding-batch.local/internal/reload-models",
        "https://embedding-search.local/internal/reload-models",
    }


@pytest.mark.asyncio
async def test_reload_fanout_identifies_failed_endpoint_after_calling_both() -> None:
    calls: list[str] = []

    def batch_urlopen(request, timeout):
        calls.append(request.full_url)
        raise URLError("batch endpoint down")

    def search_urlopen(request, timeout):
        calls.append(request.full_url)
        return _Response({"ready_model_versions": ["candidate-v2"]})

    client = ManagedEmbeddingModelReloadFanout(
        batch_client=ManagedEmbeddingModelReloadClient(
            base_url="https://embedding-batch.local",
            urlopen_func=batch_urlopen,
        ),
        search_client=ManagedEmbeddingModelReloadClient(
            base_url="https://embedding-search.local",
            urlopen_func=search_urlopen,
        ),
    )

    with pytest.raises(
        ModelReloadError,
        match="https://embedding-batch.local",
    ):
        await client.reload(trace_id=uuid4())

    assert set(calls) == {
        "https://embedding-batch.local/internal/reload-models",
        "https://embedding-search.local/internal/reload-models",
    }


@pytest.mark.asyncio
async def test_reload_fanout_calls_shared_endpoint_once() -> None:
    calls: list[str] = []

    def urlopen(request, timeout):
        calls.append(request.full_url)
        return _Response({"ready_model_versions": ["active-v1"]})

    client = ManagedEmbeddingModelReloadFanout(
        batch_client=ManagedEmbeddingModelReloadClient(
            base_url="https://embedding-shared.local/",
            urlopen_func=urlopen,
        ),
        search_client=ManagedEmbeddingModelReloadClient(
            base_url="https://embedding-shared.local",
            urlopen_func=urlopen,
        ),
    )

    result = await client.reload(trace_id=uuid4())

    assert result.ready_model_versions == frozenset({"active-v1"})
    assert calls == ["https://embedding-shared.local/internal/reload-models"]
