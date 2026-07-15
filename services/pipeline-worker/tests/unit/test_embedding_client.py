from collections.abc import Awaitable, Callable

import httpx
import pytest

from src.infra.ai import embedding_client as embedding_client_module
from src.infra.ai.embedding_client import EmbeddingClient
from src.infra.ai.google_stt_adapter import ExternalAIAdapterError
from tests.support import build_embedding_client


EventFields = dict[str, object]


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[EventFields]:
    events: list[EventFields] = []

    def capture_event(**fields: object) -> None:
        events.append(fields)

    monkeypatch.setattr(embedding_client_module, "_log_request_event", capture_event)
    return events


def _client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    sleep: Callable[[float], Awaitable[None]],
) -> EmbeddingClient:
    return EmbeddingClient(
        base_url="https://embedding.local",
        timeout_sec=5,
        max_retries=3,
        model_version="v001",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        sleep=sleep,
        jitter=lambda: 0.0,
    )


class TestEmbeddingRequestSuccess:
    @pytest.mark.asyncio
    async def test_returns_embeddings_and_logs_required_fields(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events = _capture_events(monkeypatch)
        client = build_embedding_client()

        result = await client.embed_texts(["alpha", "beta"], trace_id="trace-1")

        assert result.model_version == "v001"
        assert len(result.embeddings) == 2
        assert events[0]["event"] == "embedding.request.success"
        assert {
            "event",
            "trace_id",
            "model_version",
            "text_count",
            "attempt",
            "duration_ms",
            "status_code",
            "error_code",
            "retry_delay_seconds",
        } <= events[0].keys()
        assert events[0]["status_code"] == 200
        assert events[0]["text_count"] == 2


class TestEmbeddingRetryPolicy:
    @pytest.mark.asyncio
    async def test_retries_only_503_with_exponential_backoff_and_jitter(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events = _capture_events(monkeypatch)
        delays: list[float] = []

        async def record_sleep(delay_seconds: float) -> None:
            delays.append(delay_seconds)

        client = build_embedding_client(
            fail_embed_times=2,
            max_retries=3,
            sleep=record_sleep,
            jitter=lambda: 1.0,
        )

        result = await client.embed_texts(["alpha"], trace_id="trace-2")

        assert result.embeddings[0][0] == pytest.approx(5.0)
        assert delays == pytest.approx([1.25, 2.5])
        assert [event["event"] for event in events] == [
            "embedding.request.retry",
            "embedding.request.retry",
            "embedding.request.success",
        ]
        assert [event["attempt"] for event in events] == [1, 2, 3]
        assert events[0]["status_code"] == 503
        assert events[0]["error_code"] == "UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_503_exhaustion_logs_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events = _capture_events(monkeypatch)
        delays: list[float] = []

        async def record_sleep(delay_seconds: float) -> None:
            delays.append(delay_seconds)

        client = build_embedding_client(
            fail_embed_times=3,
            max_retries=2,
            sleep=record_sleep,
            jitter=lambda: 0.0,
        )

        with pytest.raises(ExternalAIAdapterError) as error:
            await client.embed_texts(["alpha"], trace_id="trace-exhausted")

        assert error.value.code == "UNAVAILABLE"
        assert error.value.attempt_count == 3
        assert delays == pytest.approx([1.0, 2.0])
        assert [event["event"] for event in events] == [
            "embedding.request.retry",
            "embedding.request.retry",
            "embedding.request.failed",
        ]

    @pytest.mark.asyncio
    async def test_timeout_stops_without_sending_second_request(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events = _capture_events(monkeypatch)
        requests = 0
        delays: list[float] = []

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            raise httpx.ReadTimeout("slow embedding", request=request)

        async def record_sleep(delay_seconds: float) -> None:
            delays.append(delay_seconds)

        client = _client_with_handler(timeout_handler, sleep=record_sleep)

        with pytest.raises(ExternalAIAdapterError) as error:
            await client.embed_texts(["alpha"], trace_id="trace-timeout")

        assert requests == 1
        assert delays == []
        assert error.value.code == "TIMEOUT"
        assert events[0]["event"] == "embedding.request.timeout"

    @pytest.mark.asyncio
    async def test_non_503_http_error_is_not_retried(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events = _capture_events(monkeypatch)
        requests = 0
        delays: list[float] = []

        def error_handler(request: httpx.Request) -> httpx.Response:
            nonlocal requests
            requests += 1
            return httpx.Response(500, request=request)

        async def record_sleep(delay_seconds: float) -> None:
            delays.append(delay_seconds)

        client = _client_with_handler(error_handler, sleep=record_sleep)

        with pytest.raises(httpx.HTTPStatusError):
            await client.embed_texts(["alpha"], trace_id="trace-http-error")

        assert requests == 1
        assert delays == []
        assert len(events) == 1
        assert events[0]["event"] == "embedding.request.failed"
        assert events[0]["status_code"] == 500


class TestEmbeddingValidation:
    @pytest.mark.asyncio
    async def test_rejects_empty_input(self) -> None:
        client = build_embedding_client()

        with pytest.raises(ExternalAIAdapterError):
            await client.embed_texts([], trace_id="trace-3")


class TestEmbeddingHealth:
    @pytest.mark.asyncio
    async def test_reads_ready_model_versions(self) -> None:
        client = build_embedding_client(model_version="v001")

        versions = await client.get_ready_model_versions(trace_id="trace-4")

        assert versions == ["v001"]

    @pytest.mark.asyncio
    async def test_uses_configured_ready_version(self) -> None:
        client = build_embedding_client(
            model_version="v002",
            ready_model_versions=["v001", "v002"],
            embedding_model_version="v002",
        )

        version = await client.get_model_version(trace_id="trace-5")

        assert version == "v002"

    @pytest.mark.asyncio
    async def test_rejects_health_without_ready_model_versions(self) -> None:
        client = build_embedding_client(health_payload={"status": "ok", "model_version": "v001"})

        with pytest.raises(ExternalAIAdapterError, match="ready_model_versions"):
            await client.get_ready_model_versions(trace_id="trace-6")
