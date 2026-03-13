from __future__ import annotations

from src.infra.broker import BrokerClient, BrokerMessage, BrokerPublishError


class InMemoryBrokerClient(BrokerClient):
    def __init__(self, *, failures_before_success: int = 0) -> None:
        self._failures_before_success = failures_before_success
        self.publish_attempts = 0
        self.published_messages: list[dict[str, object]] = []

    async def publish(self, message: BrokerMessage) -> int | None:
        self.publish_attempts += 1
        if self._failures_before_success > 0:
            self._failures_before_success -= 1
            raise BrokerPublishError("In-memory broker simulated a publish failure.")

        self.published_messages.append(message.to_payload())
        return len(self.published_messages)
