from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from scripts.e2e.lib.gcloud import GCloud


class HTTPRequestError(RuntimeError):
    pass


def make_jwt(*, requester_user_id: str, secret: str, admin: bool = False) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "requester_user_id": requester_user_id,
        "iat": now,
        "exp": now + 3600,
    }
    if admin:
        payload["role"] = "admin"
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64_json(header)}.{_b64_json(payload)}"
    signature = hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()
    return f"{signing_input}.{_b64(signature)}"


def decode_unverified_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("JWT must have three parts.")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    if not isinstance(payload, dict):
        raise ValueError("JWT payload must be an object.")
    return payload


class JsonHttpClient:
    def __init__(
        self,
        *,
        app_jwt: str,
        gcloud: GCloud | None = None,
        use_cloud_run_identity_token: bool = False,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._app_jwt = app_jwt
        self._gcloud = gcloud
        self._use_cloud_run_identity_token = use_cloud_run_identity_token
        self._timeout_seconds = timeout_seconds

    def post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any] | None:
        request = self._json_request(url, body)
        return _read_json_response(request, self._timeout_seconds)

    def put_bytes(self, url: str, payload: bytes, *, content_type: str) -> None:
        request = urllib.request.Request(
            url,
            data=payload,
            method="PUT",
            headers={"Content-Type": content_type},
        )
        _read_bytes_response(request, self._timeout_seconds)

    def _json_request(self, url: str, body: dict[str, Any]) -> urllib.request.Request:
        headers = {
            "Authorization": f"Bearer {self._app_jwt}",
            "Content-Type": "application/json",
        }
        if self._use_cloud_run_identity_token:
            headers["X-Serverless-Authorization"] = f"Bearer {self._identity_token(url)}"
        return urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers=headers,
        )

    def _identity_token(self, url: str) -> str:
        if self._gcloud is None:
            raise HTTPRequestError("gcloud helper is required for Cloud Run identity token auth.")
        return self._gcloud.identity_token(_audience_from_url(url))


def _read_json_response(request: urllib.request.Request, timeout_seconds: float) -> dict[str, Any] | None:
    payload = _read_bytes_response(request, timeout_seconds)
    if not payload:
        return None
    parsed = json.loads(payload.decode("utf-8"))
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise HTTPRequestError("JSON response must be an object.")
    return parsed


def _read_bytes_response(request: urllib.request.Request, timeout_seconds: float) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPRequestError(f"HTTP {exc.code} for {request.full_url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise HTTPRequestError(f"Request failed for {request.full_url}: {exc}") from exc


def _audience_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPRequestError(f"Cannot derive audience from URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _b64_json(payload: dict[str, Any]) -> str:
    return _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))


def _b64(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
