import asyncio
from abc import ABC, abstractmethod

from src.schemas.feedback_dto import FeedbackEvent


class FeedbackEventDeliveryError(RuntimeError):
    pass


class RetriableFeedbackEventDeliveryError(FeedbackEventDeliveryError):
    pass


class TerminalFeedbackEventDeliveryError(FeedbackEventDeliveryError):
    pass


class FeedbackEventDeliveryClient(ABC):
    @abstractmethod
    async def deliver(self, event: FeedbackEvent) -> None:
        raise NotImplementedError

    async def deliver_with_retry(
        self,
        event: FeedbackEvent,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.0,
    ) -> None:
        last_error: RetriableFeedbackEventDeliveryError | None = None

        for attempt_number in range(1, max_attempts + 1):
            try:
                await self.deliver(event)
                return
            except RetriableFeedbackEventDeliveryError as exc:
                last_error = exc
                if attempt_number == max_attempts:
                    break
                if retry_delay_seconds > 0:
                    await asyncio.sleep(retry_delay_seconds)
            except TerminalFeedbackEventDeliveryError:
                raise

        if last_error is None:
            raise FeedbackEventDeliveryError(
                "Feedback delivery retry loop exited without a result."
            )
        raise last_error
