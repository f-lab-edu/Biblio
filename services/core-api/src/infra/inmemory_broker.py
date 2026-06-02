from src.infra.broker import BrokerClient, BrokerPublishError, PublishableMessage


class InMemoryBrokerClient(BrokerClient):
    def __init__(self, *, failures_before_success: int = 0) -> None:
        self._failures_before_success = failures_before_success
        self.publish_attempts = 0
        self.published_messages: list[dict[str, object]] = []
        self.published_envelopes: list[tuple[str, dict[str, object]]] = []

    async def publish(self, message: PublishableMessage) -> int | None:
        self.publish_attempts += 1
        if self._failures_before_success > 0:
            self._failures_before_success -= 1
            raise BrokerPublishError("In-memory broker simulated a publish failure.")

        payload = message.to_payload()
        queue_name = message.queue_name or message.message_type
        self.published_messages.append(payload)
        self.published_envelopes.append((queue_name, payload))
        return len(self.published_messages)
