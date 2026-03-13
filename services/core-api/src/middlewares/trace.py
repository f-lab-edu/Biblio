from __future__ import annotations

from typing import Callable
from uuid import UUID, uuid4

from src.common.logging import error as log_error
from src.common.logging import info as log_info
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _coerce_uuid(value: str | None) -> UUID:
    if not value:
        return uuid4()
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return uuid4()


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Assigns a trace_id to request.state and reflects it in response headers.

    Header key: X-Trace-Id
    """

    header_name = "X-Trace-Id"

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:  # type: ignore[override]
        # Prefer existing state/header; ensure UUID format
        raw = getattr(request.state, "trace_id", None) or request.headers.get(self.header_name)
        trace_id = _coerce_uuid(raw)
        request.state.trace_id = str(trace_id)

        try:
            response = await call_next(request)
        except Exception:
            log_error(
                "http.request.failed",
                trace_id=str(trace_id),
                method=request.method,
                path=request.url.path,
                user_id=getattr(request.state, "user_id", None),
                video_id=getattr(request.state, "video_id", None),
            )
            raise

        response.headers[self.header_name] = str(trace_id)
        log_info(
            "http.request.completed",
            trace_id=str(trace_id),
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            user_id=getattr(request.state, "user_id", None),
            video_id=getattr(request.state, "video_id", None),
        )
        return response
