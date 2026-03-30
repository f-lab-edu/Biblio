from collections.abc import Callable
from uuid import UUID

import httpx
from httpx import ASGITransport

from src.core.model_state import ModelState
from src.core.settings import Settings

_SETTINGS = Settings(MODEL_ARTIFACT_PATH="test/model")


class TestTraceIdMiddleware:
    async def test_echo_provided_trace_id(
        self,
        app_factory: Callable[..., object],
        ready_model_state_factory: Callable[[str], ModelState],
    ):
        app = app_factory(
            settings=_SETTINGS,
            model_state=ready_model_state_factory("test/model"),
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            trace = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            resp = await client.get("/health", headers={"X-Trace-Id": trace})
            assert resp.headers["X-Trace-Id"] == trace

    async def test_generate_trace_id_when_missing(
        self,
        app_factory: Callable[..., object],
        ready_model_state_factory: Callable[[str], ModelState],
    ):
        app = app_factory(
            settings=_SETTINGS,
            model_state=ready_model_state_factory("test/model"),
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            resp = await client.get("/health")
            trace_id = resp.headers.get("X-Trace-Id")
            assert trace_id is not None
            UUID(trace_id)  # validates format

    async def test_invalid_trace_id_replaced(
        self,
        app_factory: Callable[..., object],
        ready_model_state_factory: Callable[[str], ModelState],
    ):
        app = app_factory(
            settings=_SETTINGS,
            model_state=ready_model_state_factory("test/model"),
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            resp = await client.get("/health", headers={"X-Trace-Id": "not-a-uuid"})
            trace_id = resp.headers["X-Trace-Id"]
            UUID(trace_id)  # must be a valid UUID (newly generated)

    async def test_error_response_includes_trace_id(
        self,
        app_factory: Callable[..., object],
    ):
        app = app_factory(
            settings=_SETTINGS,
            model_state=ModelState(),
        )
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            trace = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
            resp = await client.get("/health", headers={"X-Trace-Id": trace})
            assert resp.status_code == 503
            assert resp.headers["X-Trace-Id"] == trace
            body = resp.json()
            assert body["trace_id"] == trace
