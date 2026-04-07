from uuid import UUID, uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.common.logging import error as log_error
from src.common.logging import info as log_info


def _coerce_uuid(value: str | None) -> UUID:
    if not value:
        return uuid4()
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return uuid4()


class TraceIdMiddleware:
    """Pure ASGI middleware — no body buffering, streaming-safe."""

    header_name = "X-Trace-Id"

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Parse trace_id from request headers
        raw_trace_id: str | None = None
        for key, value in scope.get("headers", []):
            if key == b"x-trace-id":
                raw_trace_id = value.decode("latin-1")
                break

        trace_id = str(_coerce_uuid(raw_trace_id))

        # Store in scope state for request.state access
        if "state" not in scope:
            scope["state"] = {}
        scope["state"]["trace_id"] = trace_id

        status_code: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status")
                headers = MutableHeaders(scope=message)
                headers[self.header_name] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log_error(
                "http.request.failed",
                trace_id=trace_id,
                method=scope.get("method", ""),
                path=scope.get("path", ""),
                user_id=scope.get("state", {}).get("user_id"),
            )
            raise

        log_info(
            "http.request.completed",
            trace_id=trace_id,
            method=scope.get("method", ""),
            path=scope.get("path", ""),
            status_code=status_code,
            user_id=scope.get("state", {}).get("user_id"),
        )
