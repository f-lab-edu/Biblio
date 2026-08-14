from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

from scripts.test_support.http import JsonHttpClient
from scripts.test_support.video_api import VideoApiClient, VideoApiError


class _UrlOpenResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_UrlOpenResponse":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


class _FakeJsonHttpTransport:
    def __init__(self) -> None:
        self.posts: list[
            tuple[str, dict[str, Any], Mapping[str, str] | None]
        ] = []
        self.gets: list[str] = []
        self.uploads: list[tuple[str, bytes, str]] = []

    def get_json(self, url: str) -> dict[str, Any] | None:
        self.gets.append(url)
        return {"video_id": "video-1", "status": "READY"}

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        self.posts.append((url, body, headers))
        if url.endswith("/complete"):
            return {"video_id": "video-1", "status": "PENDING"}
        return {"video_id": "video-1", "signed_url": "https://storage.example/upload"}

    def put_bytes(self, url: str, payload: bytes, *, content_type: str) -> None:
        self.uploads.append((url, payload, content_type))


class TestJsonHttpClient(unittest.TestCase):
    def test_get_json_sends_application_jwt(self) -> None:
        response = _UrlOpenResponse({"video_id": "video-1", "status": "READY"})
        with patch(
            "scripts.test_support.http.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            result = JsonHttpClient(app_jwt="app-token").get_json(
                "https://core.example/api/v1/videos/video-1"
            )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer app-token")
        self.assertEqual(result, {"video_id": "video-1", "status": "READY"})

    def test_get_json_refreshes_application_token_from_provider(self) -> None:
        class _TokenProvider:
            def application_token(self) -> str:
                return "refreshed-token"

        response = _UrlOpenResponse({"video_id": "video-1", "status": "READY"})
        with patch(
            "scripts.test_support.http.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            JsonHttpClient(
                application_token_provider=_TokenProvider()
            ).get_json("https://core.example/api/v1/videos/video-1")

        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer refreshed-token",
        )


class TestVideoApiClient(unittest.TestCase):
    def test_video_lifecycle_uses_shared_api_paths(self) -> None:
        transport = _FakeJsonHttpTransport()
        client = VideoApiClient("https://core.example/", transport)

        created = client.create_local_video(
            project_id="project-1",
            title="fixture",
            category="GENERAL",
            extension=".mp4",
        )
        client.upload_bytes(str(created["signed_url"]), b"video")
        client.complete_video("video-1", 5, trace_id="trace-1")
        video = client.get_video("video-1")

        self.assertEqual(transport.posts[0][0], "https://core.example/api/v1/projects/project-1/videos")
        self.assertEqual(transport.posts[1][0], "https://core.example/api/v1/videos/video-1/complete")
        self.assertEqual(transport.posts[1][2], {"X-Trace-Id": "trace-1"})
        self.assertEqual(transport.gets, ["https://core.example/api/v1/videos/video-1"])
        self.assertEqual(video["status"], "READY")

    def test_video_response_must_match_requested_video(self) -> None:
        transport = _FakeJsonHttpTransport()
        client = VideoApiClient("https://core.example", transport)

        with self.assertRaisesRegex(VideoApiError, "does not match"):
            client.get_video("video-2")


if __name__ == "__main__":
    unittest.main()
