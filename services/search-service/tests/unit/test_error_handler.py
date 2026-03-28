"""Tests for the unhandled exception (500) handler.

Verifies that unexpected exceptions produce the Search Service error contract:
{"code": "INTERNAL_ERROR", "message": "...", "trace_id": "..."} with X-Trace-Id header.
"""

from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.trace import TraceIdMiddleware


def _create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("something broke")

    @app.get("/ok")
    async def ok() -> dict:
        return {"status": "ok"}

    return app


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=_create_app(), raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        yield client


class TestUnhandledExceptionHandler:
    async def test_returns_500_with_error_contract(self, client: AsyncClient) -> None:
        resp = await client.get("/boom")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "INTERNAL_ERROR"
        assert body["message"] == "An internal error occurred."
        assert "trace_id" in body
        UUID(body["trace_id"])  # valid UUID

    async def test_does_not_leak_internal_details(self, client: AsyncClient) -> None:
        resp = await client.get("/boom")
        body = resp.json()
        assert "something broke" not in body["message"]

    async def test_x_trace_id_header_matches_body(self, client: AsyncClient) -> None:
        resp = await client.get("/boom")
        body = resp.json()
        assert resp.headers["X-Trace-Id"] == body["trace_id"]

    async def test_echoes_client_trace_id_on_error(self, client: AsyncClient) -> None:
        sent_id = str(uuid4())
        resp = await client.get("/boom", headers={"X-Trace-Id": sent_id})
        assert resp.headers["X-Trace-Id"] == sent_id
        assert resp.json()["trace_id"] == sent_id

