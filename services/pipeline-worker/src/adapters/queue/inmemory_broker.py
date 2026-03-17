from collections import defaultdict
from uuid import uuid4

from adapters.queue.broker import BrokerClient, BrokerMessage


class InMemoryBrokerClient(BrokerClient):
    def __init__(self) -> None:
        self._queues: dict[str, list[BrokerMessage]] = defaultdict(list)
        self.acked_receipts: list[str] = []

    async def enqueue(self, queue_name: str, payload: dict) -> None:
        self._queues[queue_name].append(BrokerMessage(receipt_handle=str(uuid4()), payload=dict(payload)))

    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]:
        messages = self._queues[queue_name][:limit]
        self._queues[queue_name] = self._queues[queue_name][limit:]
        return messages

    async def ack(self, queue_name: str, receipt_handle: str) -> None:
        self.acked_receipts.append(f"{queue_name}:{receipt_handle}")

    def pending_count(self, queue_name: str) -> int:
        return len(self._queues[queue_name])
