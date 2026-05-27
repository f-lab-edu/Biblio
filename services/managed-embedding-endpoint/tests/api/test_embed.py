import asyncio
from collections.abc import Callable

import httpx

from tests.conftest import EMBEDDING_DIM, SlowRuntime, StubRuntime


class TestEmbedSuccess:
    async def test_single_text_returns_one_embedding(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post(
            "/embed",
            json={"texts": ["hello"], "model_version": "test-model"},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["embeddings"]) == 1
        assert len(body["embeddings"][0]) == EMBEDDING_DIM

    async def test_batch_texts_returns_matching_count(
        self, ready_client: httpx.AsyncClient
    ):
        texts = ["one", "two", "three"]
        response = await ready_client.post(
            "/embed",
            json={"texts": texts, "model_version": "test-model"},
        )

        assert response.status_code == 200
        body = response.json()
        assert len(body["embeddings"]) == len(texts)

    async def test_response_has_trace_id_header(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post(
            "/embed",
            json={"texts": ["hello"], "model_version": "test-model"},
        )

        assert "x-trace-id" in response.headers

    async def test_response_does_not_contain_model_version(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post(
            "/embed",
            json={"texts": ["hello"], "model_version": "test-model"},
        )

        body = response.json()
        assert "model_version" not in body

    async def test_routes_to_requested_model_version(
        self,
        app_factory: Callable[..., object],
        inference_service_factory: Callable[..., object],
        ready_model_state_factory: Callable[[str], object],
        settings_factory: Callable[..., object],
    ):
        settings = settings_factory()
        model_state = ready_model_state_factory("fake-20260526T143000KST")
        model_state.mark_ready("fake-20260526T144000KST")
        service = inference_service_factory(
            settings=settings,
            model_state=model_state,
            runtimes={
                "fake-20260526T143000KST": StubRuntime(offset=0.0),
                "fake-20260526T144000KST": StubRuntime(offset=10.0),
            },
        )
        app = app_factory(
            settings=settings,
            model_state=model_state,
            inference_service=service,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            response = await client.post(
                "/embed",
                json={
                    "texts": ["abcd"],
                    "model_version": "fake-20260526T144000KST",
                },
            )

        assert response.status_code == 200
        body = response.json()
        assert body["embeddings"][0] == [14.0] * EMBEDDING_DIM


class TestEmbedNotReady:
    async def test_returns_503_when_model_not_ready(
        self, not_ready_client: httpx.AsyncClient
    ):
        response = await not_ready_client.post(
            "/embed",
            json={"texts": ["hello"], "model_version": "test-model"},
        )

        assert response.status_code == 503

    async def test_returns_503_for_unknown_model_version(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post(
            "/embed",
            json={"texts": ["hello"], "model_version": "missing-model"},
        )

        assert response.status_code == 503


class TestEmbedValidation:
    async def test_empty_texts_returns_400(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post(
            "/embed",
            json={"texts": [], "model_version": "test-model"},
        )

        assert response.status_code == 400

    async def test_missing_texts_returns_400(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post("/embed", json={})

        assert response.status_code == 400

    async def test_missing_model_version_returns_400(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post("/embed", json={"texts": ["hello"]})

        assert response.status_code == 400

    async def test_empty_string_in_texts_returns_400(
        self, ready_client: httpx.AsyncClient
    ):
        response = await ready_client.post(
            "/embed",
            json={"texts": [""], "model_version": "test-model"},
        )

        assert response.status_code == 400

    async def test_texts_exceeding_max_returns_400(
        self, ready_client: httpx.AsyncClient
    ):
        texts = ["text"] * 33  # default max is 32
        response = await ready_client.post(
            "/embed",
            json={"texts": texts, "model_version": "test-model"},
        )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_ARGUMENT"


class TestEmbedGuardrails:
    async def test_individual_text_too_long_returns_400(
        self,
        app_factory: Callable[..., object],
        inference_service_factory: Callable[..., object],
        ready_model_state_factory: Callable[[str], object],
        settings_factory: Callable[..., object],
    ):
        settings = settings_factory(max_text_length_chars=10)
        model_state = ready_model_state_factory()
        service = inference_service_factory(
            settings=settings,
            model_state=model_state,
            runtime=StubRuntime(),
        )
        app = app_factory(
            settings=settings,
            model_state=model_state,
            inference_service=service,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            response = await client.post(
                "/embed",
                json={"texts": ["a" * 20], "model_version": "test-model"},
            )

        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "INVALID_ARGUMENT"

    async def test_payload_too_large_returns_413(
        self,
        app_factory: Callable[..., object],
        inference_service_factory: Callable[..., object],
        ready_model_state_factory: Callable[[str], object],
        settings_factory: Callable[..., object],
    ):
        settings = settings_factory(max_payload_bytes=32)
        model_state = ready_model_state_factory()
        service = inference_service_factory(
            settings=settings,
            model_state=model_state,
            runtime=StubRuntime(),
        )
        app = app_factory(
            settings=settings,
            model_state=model_state,
            inference_service=service,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            response = await client.post(
                "/embed",
                json={"texts": ["a" * 100], "model_version": "test-model"},
            )

        assert response.status_code == 413
        body = response.json()
        assert body["code"] == "PAYLOAD_TOO_LARGE"
        assert "trace_id" in body


class TestEmbedAdmissionControl:
    async def test_second_request_rejected_before_thread_work_scales(
        self,
        app_factory: Callable[..., object],
        inference_service_factory: Callable[..., object],
        ready_model_state_factory: Callable[[str], object],
        settings_factory: Callable[..., object],
        slow_runtime: SlowRuntime,
    ):
        settings = settings_factory(max_concurrency=1)
        model_state = ready_model_state_factory()
        service = inference_service_factory(
            settings=settings,
            model_state=model_state,
            runtime=slow_runtime,
        )
        app = app_factory(
            settings=settings,
            model_state=model_state,
            inference_service=service,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            first = asyncio.create_task(
                client.post(
                    "/embed",
                    json={"texts": ["first"], "model_version": "test-model"},
                )
            )
            await asyncio.to_thread(slow_runtime.entered.wait, 5)

            second = await client.post(
                "/embed",
                json={"texts": ["second"], "model_version": "test-model"},
            )

            slow_runtime.release.set()
            first_response = await first

        assert second.status_code == 503
        assert second.json()["code"] == "SERVICE_UNAVAILABLE"
        assert first_response.status_code == 200
