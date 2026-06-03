from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.runtime.messages import build_reembedding_request
from src.runtime.queue import BrokerClient


class BrokerReembeddingSink:
    def __init__(self, *, broker: BrokerClient, queue_name: str) -> None:
        self._broker = broker
        self._queue_name = queue_name

    async def request_reembedding(
        self, *, video_id: UUID, target_model_version: str, target_index_name: str
    ) -> None:
        payload = build_reembedding_request(
            video_id=video_id,
            target_model_version=target_model_version,
            target_index_name=target_index_name,
            trace_id=uuid4(),
            issued_at=datetime.now(UTC),
        )
        await self._broker.enqueue(self._queue_name, payload)
