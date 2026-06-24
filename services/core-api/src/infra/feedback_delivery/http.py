import asyncio
import json
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from src.infra.feedback_delivery.client import (
    FeedbackEventDeliveryClient,
    FeedbackEventDeliveryError,
    RetriableFeedbackEventDeliveryError,
    TerminalFeedbackEventDeliveryError,
)
from src.schemas.feedback_dto import FeedbackEvent

SendRequest = Callable[[Request, float], int]
# audience(수신 서비스 주소)를 받아 Google ID 토큰 문자열을 돌려준다.
IdTokenProvider = Callable[[str], str]


class HttpFeedbackEventDeliveryClient(FeedbackEventDeliveryClient):
    def __init__(
        self,
        *,
        endpoint_url: str,
        timeout_seconds: float = 2.0,
        send_request: SendRequest | None = None,
        id_token_provider: IdTokenProvider | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._timeout_seconds = timeout_seconds
        self._send_request = send_request or _send_request
        self._id_token_provider = id_token_provider

    async def deliver(self, event: FeedbackEvent) -> None:
        request = self._build_request(event)
        try:
            status_code = await asyncio.to_thread(
                self._send_request,
                request,
                self._timeout_seconds,
            )
        except OSError as exc:
            raise RetriableFeedbackEventDeliveryError(
                "Feedback delivery request failed."
            ) from exc

        if status_code < 200 or status_code >= 300:
            self._raise_for_rejected_status(status_code)

    def _build_request(self, event: FeedbackEvent) -> Request:
        payload = json.dumps(
            event.model_dump(mode="json"),
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = self._resolve_auth_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return Request(
            self._endpoint_url,
            data=payload,
            headers=headers,
            method="POST",
        )

    def _resolve_auth_token(self) -> str | None:
        if self._id_token_provider is None:
            return None
        return self._id_token_provider(self._audience())

    def _audience(self) -> str:
        # 토큰 수신자는 FIP 서비스 주소(경로 제외)다. 예: https://feedback-...run.app
        parts = urlsplit(self._endpoint_url)
        return urlunsplit((parts.scheme, parts.netloc, "", "", ""))

    def _raise_for_rejected_status(self, status_code: int) -> None:
        if status_code >= 500:
            raise RetriableFeedbackEventDeliveryError(
                f"Feedback delivery was rejected with status {status_code}."
            )
        raise TerminalFeedbackEventDeliveryError(
            f"Feedback delivery was rejected with status {status_code}."
        )


def _send_request(request: Request, timeout_seconds: float) -> int:
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except URLError as exc:
        raise RetriableFeedbackEventDeliveryError(
            "Feedback delivery request failed."
        ) from exc


def fetch_google_id_token(audience: str) -> str:
    """배포 환경에서 메타데이터 서버로부터 audience용 ID 토큰을 발급받는다."""
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import id_token

    return id_token.fetch_id_token(GoogleAuthRequest(), audience)
