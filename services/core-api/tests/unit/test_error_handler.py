from __future__ import annotations

import asyncio
from uuid import uuid4

from starlette.requests import Request

from src.common.metrics import REGISTRY
from src.infra.db.cursor import CursorDecodeError
from src.middlewares.error_handler import (
    ApiError,
    InvalidArgumentError,
    api_error_handler,
    cursor_decode_error_handler,
    validation_error_handler,
)


def _build_request(headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    scope = {"type": "http", "headers": headers or []}
    return Request(scope)


def test_api_error_handler_sets_trace_header_and_payload_trace_id() -> None:
    trace = str(uuid4())
    request = _build_request([(b"x-trace-id", trace.encode("utf-8"))])
    exc = ApiError("boom")

    response = asyncio.run(api_error_handler(request, exc))
    body = response.body.decode("utf-8")

    assert response.headers.get("X-Trace-Id") == trace
    assert f'"trace_id":"{trace}"' in body
    assert '"code":"INTERNAL_ERROR"' in body


def test_validation_error_handler_sets_header_and_invalid_argument() -> None:
    # Simulate a pydantic/fastapi validation error with minimal surface by passing empty errors list
    class DummyValidationError(Exception):
        def __init__(self) -> None:
            self._errors = []

        def errors(self) -> list[dict[str, object]]:
            return self._errors

    # Starlette/FastAPI raises RequestValidationError, but our handler only needs .errors()
    request = _build_request()
    response = asyncio.run(validation_error_handler(request, DummyValidationError()))  # type: ignore[arg-type]
    body = response.body.decode("utf-8")

    assert response.status_code == 400
    assert response.headers.get("X-Trace-Id") is not None
    assert '"code":"INVALID_ARGUMENT"' in body


def test_cursor_decode_error_increments_metric_and_maps_to_400() -> None:
    before = REGISTRY.snapshot()["counters"].get("cursor_decode_fail_count", 0)
    request = _build_request()
    response = asyncio.run(cursor_decode_error_handler(request, CursorDecodeError("Invalid cursor token.")))
    after = REGISTRY.snapshot()["counters"].get("cursor_decode_fail_count", 0)

    assert response.status_code == 400
    assert after == before + 1

