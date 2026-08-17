from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(slots=True)
class BrokerMessage:
    receipt_handle: str
    payload: dict
    enqueued_at: datetime
    read_ct: int


class BrokerClient(Protocol):
    async def enqueue(self, queue_name: str, payload: dict) -> None: ...

    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]: ...

    async def ack(self, queue_name: str, receipt_handle: str) -> None: ...
