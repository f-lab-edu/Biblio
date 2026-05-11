import asyncio
import json
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.infra.feedback_delivery.client import (
    FeedbackEventDeliveryClient,
    FeedbackEventDeliveryError,
    RetriableFeedbackEventDeliveryError,
    TerminalFeedbackEventDeliveryError,
)
from src.schemas.feedback_dto import FeedbackEvent

SendRequest = Callable[[Request, float], int]


class HttpFeedbackEventDeliveryClient(FeedbackEventDeliveryClient):
    def __init__(
        self,
        *,
        endpoint_url: str,
        timeout_seconds: float = 2.0,
        send_request: SendRequest | None = None,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._timeout_seconds = timeout_seconds
        self._send_request = send_request or _send_request

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
        return Request(
            self._endpoint_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

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
