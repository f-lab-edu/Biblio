from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol

import asyncpg


@dataclass(frozen=True, slots=True)
class BrokerMessage:
    receipt_handle: str
    payload: dict[str, Any]


class BrokerClient(Protocol):
    async def enqueue(self, queue_name: str, payload: dict[str, Any]) -> None: ...

    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]: ...

    async def ack(self, queue_name: str, receipt_handle: str) -> None: ...


class InMemoryBrokerClient:
    def __init__(self) -> None:
        self._queues: dict[str, list[BrokerMessage]] = defaultdict(list)
        self.acked_receipts: list[str] = []
        self._next_receipt = 0

    async def enqueue(self, queue_name: str, payload: dict[str, Any]) -> None:
        receipt = str(self._next_receipt)
        self._next_receipt += 1
        self._queues[queue_name].append(BrokerMessage(receipt_handle=receipt, payload=dict(payload)))

    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]:
        messages = self._queues[queue_name][:limit]
        self._queues[queue_name] = self._queues[queue_name][limit:]
        return messages

    async def ack(self, queue_name: str, receipt_handle: str) -> None:
        self.acked_receipts.append(f"{queue_name}:{receipt_handle}")

    def pending_count(self, queue_name: str) -> int:
        return len(self._queues[queue_name])


class PGMQBrokerClient:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def enqueue(self, queue_name: str, payload: dict[str, Any]) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("SELECT pgmq.send($1, $2::jsonb)", queue_name, json.dumps(payload))

    async def consume(self, queue_name: str, *, limit: int = 1) -> list[BrokerMessage]:
        async with self._pool.acquire() as connection:
            rows = await connection.fetch("SELECT * FROM pgmq.read($1, $2, 30)", queue_name, limit)
        return [
            BrokerMessage(receipt_handle=str(row["msg_id"]), payload=_normalize_payload(row["message"]))
            for row in rows
        ]

    async def ack(self, queue_name: str, receipt_handle: str) -> None:
        async with self._pool.acquire() as connection:
            await connection.execute("SELECT pgmq.archive($1, $2::bigint)", queue_name, int(receipt_handle))


async def ensure_pgmq_queues(pool: Any, queue_names: list[str]) -> None:
    async with pool.acquire() as connection:
        for name in queue_names:
            try:
                await connection.execute("SELECT pgmq.create($1)", name)
            except asyncpg.UniqueViolationError:
                pass


def to_asyncpg_dsn(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def _normalize_payload(raw_payload: Any) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        return raw_payload
    if isinstance(raw_payload, str):
        parsed = json.loads(raw_payload)
        if not isinstance(parsed, dict):
            raise TypeError("PGMQ message payload must decode to a JSON object")
        return parsed
    return dict(raw_payload)
