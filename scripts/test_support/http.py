from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any, Protocol


class HTTPRequestError(RuntimeError):
    pass


class IdentityTokenProvider(Protocol):
    def identity_token(self, audience: str) -> str: ...


class ApplicationTokenProvider(Protocol):
    def application_token(self) -> str: ...


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
    signature = hmac.new(
        secret.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
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
        app_jwt: str | None = None,
        application_token_provider: ApplicationTokenProvider | None = None,
        identity_token_provider: IdentityTokenProvider | None = None,
        use_cloud_run_identity_token: bool = False,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._app_jwt = app_jwt
        self._application_token_provider = application_token_provider
        self._identity_token_provider = identity_token_provider
        self._use_cloud_run_identity_token = use_cloud_run_identity_token
        self._timeout_seconds = timeout_seconds

    def get_json(self, url: str) -> dict[str, Any] | None:
        request = urllib.request.Request(
            url,
            method="GET",
            headers=self._authorized_headers(url),
        )
        return _read_json_response(request, self._timeout_seconds)

    def post_json(
        self,
        url: str,
        body: dict[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                **(headers or {}),
                **self._authorized_headers(url),
                "Content-Type": "application/json",
            },
        )
        return _read_json_response(request, self._timeout_seconds)

    def put_bytes(
        self,
        url: str,
        payload: bytes,
        *,
        headers: Mapping[str, str],
    ) -> None:
        request = urllib.request.Request(
            url,
            data=payload,
            method="PUT",
            headers=dict(headers),
        )
        _read_bytes_response(request, self._timeout_seconds)

    def _authorized_headers(self, url: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._application_token()}"}
        if self._use_cloud_run_identity_token:
            headers["X-Serverless-Authorization"] = (
                f"Bearer {self._identity_token(url)}"
            )
        return headers

    def _application_token(self) -> str:
        if self._application_token_provider is not None:
            return self._application_token_provider.application_token()
        if self._app_jwt:
            return self._app_jwt
        raise HTTPRequestError(
            "app_jwt or application_token_provider is required for authentication."
        )

    def _identity_token(self, url: str) -> str:
        if self._identity_token_provider is None:
            raise HTTPRequestError(
                "identity token provider is required for Cloud Run authentication."
            )
        return self._identity_token_provider.identity_token(_audience_from_url(url))


def _read_json_response(
    request: urllib.request.Request,
    timeout_seconds: float,
) -> dict[str, Any] | None:
    payload = _read_bytes_response(request, timeout_seconds)
    if not payload:
        return None
    parsed = json.loads(payload.decode("utf-8"))
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        raise HTTPRequestError("JSON response must be an object.")
    return parsed


def _read_bytes_response(
    request: urllib.request.Request,
    timeout_seconds: float,
) -> bytes:
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise HTTPRequestError(
            f"HTTP {exc.code} for {request.full_url}: {body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPRequestError(f"Request failed for {request.full_url}: {exc}") from exc


def _audience_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPRequestError(f"Cannot derive audience from URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}"


def _b64_json(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64(encoded)


def _b64(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
