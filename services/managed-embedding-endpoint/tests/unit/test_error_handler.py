from collections.abc import Callable

import httpx
from httpx import ASGITransport

from src.core.model_state import ModelState
from src.core.settings import Settings

_SETTINGS = Settings(MODEL_ARTIFACT_PATH="test/model")


class TestApiErrorHandler:
    async def test_service_unavailable_shape(
        self,
        app_factory: Callable[..., object],
    ):
        app = app_factory(settings=_SETTINGS, model_state=ModelState())
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 503
            body = resp.json()
            assert body["code"] == "SERVICE_UNAVAILABLE"
            assert "message" in body
            assert "trace_id" in body

    async def test_internal_error_returns_500(self):
        from fastapi import FastAPI

        from src.middlewares.error_handler import ApiError, register_exception_handlers
        from src.middlewares.trace import TraceIdMiddleware

        app = FastAPI()
        app.add_middleware(TraceIdMiddleware)
        register_exception_handlers(app)

        @app.get("/blow-up")
        async def blow_up():
            raise ApiError()

        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
            resp = await client.get("/blow-up")
            assert resp.status_code == 500
            body = resp.json()
            assert body["code"] == "INTERNAL_ERROR"
            assert "trace_id" in body


class TestValidationErrorHandler:
    async def test_missing_texts_returns_400(
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
            resp = await client.post("/embed", json={})
            assert resp.status_code == 400
            body = resp.json()
            assert body["code"] == "INVALID_ARGUMENT"
            assert "trace_id" in body

    async def test_empty_texts_returns_400(
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
            resp = await client.post("/embed", json={"texts": []})
            assert resp.status_code == 400
            body = resp.json()
            assert body["code"] == "INVALID_ARGUMENT"

    async def test_invalid_json_returns_400(
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
            resp = await client.post(
                "/embed",
                content="not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 400
            body = resp.json()
            assert body["code"] == "INVALID_ARGUMENT"
