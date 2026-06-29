from uuid import uuid4

import pytest

from src.release import serving_reload
from src.release.serving_reload import SearchServiceServingTargetReloader


@pytest.mark.asyncio
async def test_search_service_reloader_attaches_bearer_token_with_service_audience(
    monkeypatch,
) -> None:
    sent_requests = []
    seen_audiences = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

        def read(self) -> bytes:
            return b"{}"

    def urlopen(request, timeout):
        sent_requests.append(request)
        return FakeResponse()

    def id_token_provider(audience):
        seen_audiences.append(audience)
        return "id-token-123"

    monkeypatch.setattr(serving_reload, "urlopen", urlopen)
    reloader = SearchServiceServingTargetReloader(
        base_url="https://search-service-xyz.run.app",
        id_token_provider=id_token_provider,
    )

    await reloader.reload(trace_id=uuid4())

    assert seen_audiences == ["https://search-service-xyz.run.app"]
    assert sent_requests[0].get_header("Authorization") == "Bearer id-token-123"
