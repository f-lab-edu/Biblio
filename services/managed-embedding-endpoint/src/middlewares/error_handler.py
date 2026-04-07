from dataclasses import asdict, dataclass
from uuid import uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


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


class PayloadTooLargeError(ApiError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "PAYLOAD_TOO_LARGE"
    message = "Request payload exceeds the maximum allowed size."


class ServiceUnavailableError(ApiError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVICE_UNAVAILABLE"
    message = "The service is temporarily unavailable."


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


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
