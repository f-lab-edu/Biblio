import json
from collections.abc import Mapping
from typing import Any

from src.infra.queue.broker import BrokerClient, BrokerMessage


class PGMQBrokerClient(BrokerClient):
    def __init__(self, pool: Any, vt_by_queue: dict[str, int]) -> None:
        self._pool = pool
        self._vt_by_queue = vt_by_queue

    async def enqueue(self, queue_name: str, payload: dict) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("SELECT pgmq.send($1, $2::jsonb)", queue_name, payload)

    # pgmq에서 메세지 가져옴
    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT * FROM pgmq.read($1, $2, $3)",
                queue_name,
                self._vt_by_queue[queue_name],
                limit,
            )
        return [self._to_broker_message(row) for row in rows]

    # consume에서 가져온 한행을 내부 메세지로 변환
    def _to_broker_message(
        self,
        row: Mapping[str, Any],
    ) -> BrokerMessage:
        return BrokerMessage(
            receipt_handle=str(row["msg_id"]),
            payload=self._normalize_payload(row["message"]),
            enqueued_at=row["enqueued_at"],
            read_ct=int(row["read_ct"]),
        )

    async def ack(self, queue_name: str, receipt_handle: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("SELECT pgmq.archive($1, $2::bigint)", queue_name, int(receipt_handle))

    @staticmethod
    def _normalize_payload(raw_payload: Any) -> dict:
        if isinstance(raw_payload, dict):
            return raw_payload
        if isinstance(raw_payload, str):
            parsed = json.loads(raw_payload)
            if not isinstance(parsed, dict):
                raise TypeError("PGMQ message payload must decode to a JSON object")
            return parsed
        return dict(raw_payload)
