from __future__ import annotations

import pytest

from adapters.queue.inmemory_broker import InMemoryBrokerClient
from adapters.queue.pgmq_client import PGMQBrokerClient


@pytest.mark.asyncio
async def test_inmemory_broker_round_trip() -> None:
    broker = InMemoryBrokerClient()
    await broker.enqueue("PREPROCESS_REQUEST", {"message_type": "PREPROCESS_REQUEST"})

    messages = await broker.consume("PREPROCESS_REQUEST")

    assert len(messages) == 1
    await broker.ack("PREPROCESS_REQUEST", messages[0].receipt_handle)
    assert broker.acked_receipts == [f"PREPROCESS_REQUEST:{messages[0].receipt_handle}"]


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, query: str, *args):
        self.executed.append((query, args))

    async def fetch(self, query: str, *args):
        self.executed.append((query, args))
        return [{"msg_id": 1, "message": {"message_type": "DELETE_REQUEST"}}]


class _Acquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakePool:
    def __init__(self) -> None:
        self.connection = _FakeConnection()

    def acquire(self):
        return _Acquire(self.connection)


@pytest.mark.asyncio
async def test_pgmq_client_shapes_messages() -> None:
    pool = _FakePool()
    broker = PGMQBrokerClient(pool)

    messages = await broker.consume("DELETE_REQUEST")

    assert messages[0].receipt_handle == "1"
    assert messages[0].payload["message_type"] == "DELETE_REQUEST"
