from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class JsonHttpTransport(Protocol):
    def get_json(self, url: str) -> dict[str, Any] | None: ...

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None: ...

    def put_bytes(
        self,
        url: str,
        payload: bytes,
        *,
        headers: Mapping[str, str],
    ) -> None: ...


class VideoApiError(RuntimeError):
    pass


class VideoApiClient:
    def __init__(self, service_url: str, http: JsonHttpTransport) -> None:
        self._service_url = service_url.rstrip("/")
        self._http = http

    def create_local_video(
        self,
        *,
        project_id: str,
        title: str,
        category: str,
        extension: str,
    ) -> dict[str, Any]:
        response = self._http.post_json(
            f"{self._service_url}/api/v1/projects/{project_id}/videos",
            {
                "title": title,
                "category": category,
                "input_type": "LOCAL_FILE",
                "extension": extension,
            },
        )
        return _require_fields(
            response,
            "create video",
            "video_id",
            "signed_url",
            "upload_headers",
        )

    def upload_local_video(
        self,
        create_response: Mapping[str, Any],
        payload: bytes,
    ) -> None:
        headers = _require_upload_headers(create_response.get("upload_headers"))
        self._http.put_bytes(
            str(create_response["signed_url"]),
            payload,
            headers=headers,
        )

    def complete_video(
        self,
        video_id: str,
        size_bytes: int,
        *,
        trace_id: str | None = None,
    ) -> dict[str, Any]:
        response = self._http.post_json(
            f"{self._service_url}/api/v1/videos/{video_id}/complete",
            {"size_bytes": size_bytes},
            headers={"X-Trace-Id": trace_id} if trace_id is not None else None,
        )
        return _require_video(response, "complete video", video_id)

    def get_video(self, video_id: str) -> dict[str, Any]:
        response = self._http.get_json(
            f"{self._service_url}/api/v1/videos/{video_id}"
        )
        return _require_video(response, "get video", video_id, "status")


def _require_video(
    response: dict[str, Any] | None,
    operation: str,
    video_id: str,
    *required_fields: str,
) -> dict[str, Any]:
    validated = _require_fields(response, operation, "video_id", *required_fields)
    if str(validated["video_id"]) != video_id:
        raise VideoApiError(
            f"{operation} response video_id does not match {video_id}: {validated!r}"
        )
    return validated


def _require_fields(
    response: dict[str, Any] | None,
    operation: str,
    *required_fields: str,
) -> dict[str, Any]:
    if response is None or any(field not in response for field in required_fields):
        raise VideoApiError(
            f"{operation} response is missing required fields: {response!r}"
        )
    return response


def _require_upload_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or not isinstance(header_value, str)
        for name, header_value in value.items()
    ):
        raise VideoApiError(f"create video response has invalid upload_headers: {value!r}")
    return value
