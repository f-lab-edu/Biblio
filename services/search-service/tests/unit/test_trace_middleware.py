from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middlewares.trace import TraceIdMiddleware


def _create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/test")
    async def endpoint() -> dict:
        return {"ok": True}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_create_app())


class TestTraceIdMiddleware:
    def test_generates_trace_id_when_absent(self, client: TestClient) -> None:
        resp = client.get("/test")
        assert resp.status_code == 200
        trace_id = resp.headers.get("X-Trace-Id")
        assert trace_id is not None
        UUID(trace_id)  # should not raise

    def test_echoes_valid_trace_id(self, client: TestClient) -> None:
        sent_id = str(uuid4())
        resp = client.get("/test", headers={"X-Trace-Id": sent_id})
        assert resp.headers["X-Trace-Id"] == sent_id

    def test_replaces_invalid_trace_id(self, client: TestClient) -> None:
        resp = client.get("/test", headers={"X-Trace-Id": "not-a-uuid"})
        trace_id = resp.headers["X-Trace-Id"]
        UUID(trace_id)  # valid UUID
        assert trace_id != "not-a-uuid"

