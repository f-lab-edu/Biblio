from src.infra.feedback_delivery.client import (
    FeedbackEventDeliveryClient,
    RetriableFeedbackEventDeliveryError,
)
from src.schemas.feedback_dto import FeedbackEvent


class InMemoryFeedbackEventDeliveryClient(FeedbackEventDeliveryClient):
    def __init__(self, *, failures_before_success: int = 0) -> None:
        self._failures_before_success = failures_before_success
        self.delivery_attempts = 0
        self.delivered_events: list[dict[str, object]] = []

    async def deliver(self, event: FeedbackEvent) -> None:
        """Async to match the production feedback delivery interface."""
        self.delivery_attempts += 1
        if self._failures_before_success > 0:
            self._failures_before_success -= 1
            raise RetriableFeedbackEventDeliveryError(
                "In-memory feedback delivery simulated a delivery failure."
            )

        self.delivered_events.append(event.model_dump(mode="json"))
