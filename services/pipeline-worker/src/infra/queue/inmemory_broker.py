from collections import defaultdict
from datetime import UTC, datetime

from src.infra.queue.broker import BrokerClient, BrokerMessage


class InMemoryBrokerClient(BrokerClient):
    def __init__(self) -> None:
        self._queues: dict[str, list[BrokerMessage]] = defaultdict(list)
        self.acked_receipts: list[str] = []
        self._next_message_id = 1

    async def enqueue(self, queue_name: str, payload: dict) -> None:
        message_id = self._next_message_id
        self._next_message_id += 1
        self._queues[queue_name].append(
            BrokerMessage(
                receipt_handle=str(message_id),
                payload=dict(payload),
                enqueued_at=datetime.now(UTC),
                read_ct=1,
            )
        )

    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]:
        messages = self._queues[queue_name][:limit]
        self._queues[queue_name] = self._queues[queue_name][limit:]
        return messages

    async def ack(self, queue_name: str, receipt_handle: str) -> None:
        self.acked_receipts.append(f"{queue_name}:{receipt_handle}")

    def pending_count(self, queue_name: str) -> int:
        return len(self._queues[queue_name])
