from urllib.request import Request

import pytest

from src.release import readiness
from src.release.readiness import (
    ManagedEmbeddingReadinessClient,
    ManagedEmbeddingReadinessFanout,
)


class _Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


@pytest.mark.asyncio
async def test_readiness_fanout_requires_both_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready_payloads = {
        "https://embedding-batch.local/health": b'{"ready_model_versions":["candidate-v2"]}',
        "https://embedding-search.local/health": b'{"ready_model_versions":["candidate-v2"]}',
    }

    def urlopen(request: Request, timeout: float) -> _Response:
        return _Response(ready_payloads[request.full_url])

    monkeypatch.setattr(readiness, "urlopen", urlopen)
    client = ManagedEmbeddingReadinessFanout(
        batch_client=ManagedEmbeddingReadinessClient(
            base_url="https://embedding-batch.local"
        ),
        search_client=ManagedEmbeddingReadinessClient(
            base_url="https://embedding-search.local"
        ),
    )

    assert await client.is_candidate_ready(model_version="candidate-v2") is True

    ready_payloads["https://embedding-search.local/health"] = (
        b'{"ready_model_versions":[]}'
    )

    assert await client.is_ready(model_version="candidate-v2") is False


@pytest.mark.asyncio
async def test_readiness_fanout_checks_shared_endpoint_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def urlopen(request: Request, timeout: float) -> _Response:
        calls.append(request.full_url)
        return _Response(b'{"ready_model_versions":["active-v1"]}')

    monkeypatch.setattr(readiness, "urlopen", urlopen)
    client = ManagedEmbeddingReadinessFanout(
        batch_client=ManagedEmbeddingReadinessClient(
            base_url="https://embedding-shared.local/"
        ),
        search_client=ManagedEmbeddingReadinessClient(
            base_url="https://embedding-shared.local"
        ),
    )

    assert await client.is_candidate_ready(model_version="active-v1") is True
    assert calls == ["https://embedding-shared.local/health"]
