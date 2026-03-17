from typing import Any

from adapters.queue.broker import BrokerClient, BrokerMessage


class PGMQBrokerClient(BrokerClient):
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def enqueue(self, queue_name: str, payload: dict) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("SELECT pgmq.send($1, $2::jsonb)", queue_name, payload)

    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT * FROM pgmq.read($1, $2, 30)",
                queue_name,
                limit,
            )
        return [
            BrokerMessage(receipt_handle=str(row["msg_id"]), payload=dict(row["message"]))
            for row in rows
        ]

    async def ack(self, queue_name: str, receipt_handle: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("SELECT pgmq.archive($1, $2::bigint)", queue_name, int(receipt_handle))
