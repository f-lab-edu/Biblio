from __future__ import annotations

from dataclasses import asdict, dataclass
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from src.infra.db.cursor import CursorDecodeError
from src.common.metrics import inc_cursor_decode_fail


@dataclass(slots=True)
class ErrorPayload:
    code: str
    message: str
    trace_id: str


class ApiError(Exception):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    code = "INTERNAL_ERROR"
    message = "An internal error occurred."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message


class InvalidArgumentError(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "INVALID_ARGUMENT"
    message = "The request arguments are invalid."


class AuthenticationError(ApiError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"
    message = "Authentication credentials are invalid."


class ForbiddenError(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    message = "You do not have access to this resource."


class NotFoundError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class ConflictError(ApiError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    message = "The request conflicts with the current resource state."


class NotImplementedApiError(ApiError):
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "NOT_IMPLEMENTED"
    message = "This endpoint is a skeleton and is not implemented yet."


def ensure_trace_id(request: Request) -> str:
    trace_id = getattr(request.state, "trace_id", None) or request.headers.get("X-Trace-Id")
    if trace_id is None:
        trace_id = str(uuid4())
    request.state.trace_id = trace_id
    return trace_id


def _payload(request: Request, code: str, message: str) -> ErrorPayload:
    return ErrorPayload(code=code, message=message, trace_id=ensure_trace_id(request))


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    payload = _payload(request, exc.code, exc.message)
    response = JSONResponse(status_code=exc.status_code, content=asdict(payload))
    response.headers["X-Trace-Id"] = payload.trace_id
    return response


async def validation_error_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else None
    message = first_error.get("msg", "Request validation failed.") if first_error else "Request validation failed."
    payload = _payload(request, InvalidArgumentError.code, message)
    response = JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=asdict(payload))
    response.headers["X-Trace-Id"] = payload.trace_id
    return response


async def cursor_decode_error_handler(request: Request, exc: CursorDecodeError) -> JSONResponse:
    # Count and map to the standard INVALID_ARGUMENT contract.
    inc_cursor_decode_fail()
    payload = _payload(request, InvalidArgumentError.code, str(exc) or "Invalid cursor token.")
    response = JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=asdict(payload))
    response.headers["X-Trace-Id"] = payload.trace_id
    return response


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(CursorDecodeError, cursor_decode_error_handler)
