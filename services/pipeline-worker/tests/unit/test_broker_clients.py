from datetime import UTC, datetime
from typing import Any

import pytest

from src.infra.queue.inmemory_broker import InMemoryBrokerClient
from src.infra.queue.pgmq_client import PGMQBrokerClient


class _FakeConnection:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []
        self._rows = rows or []

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.fetched.append((query, args))
        return self._rows


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def acquire(self) -> "_FakePool":
        return self

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_inmemory_broker_round_trip() -> None:
    broker = InMemoryBrokerClient()
    await broker.enqueue("PREPROCESS_REQUEST", {"message_type": "PREPROCESS_REQUEST"})

    messages = await broker.consume("PREPROCESS_REQUEST")

    assert len(messages) == 1
    assert messages[0].enqueued_at.tzinfo is UTC
    assert messages[0].read_ct == 1
    await broker.ack("PREPROCESS_REQUEST", messages[0].receipt_handle)
    assert broker.acked_receipts == [f"PREPROCESS_REQUEST:{messages[0].receipt_handle}"]


@pytest.mark.parametrize(
    ("queue_name", "expected_vt"),
    [
        ("PREPROCESS_REQUEST", 1800),
        ("DELETE_REQUEST", 300),
        ("PROJECT_DELETE_REQUEST", 300),
    ],
)
@pytest.mark.asyncio
async def test_pgmq_consume_passes_queue_vt_second_and_limit_third(
    queue_name: str,
    expected_vt: int,
) -> None:
    connection = _FakeConnection()
    broker = PGMQBrokerClient(
        _FakePool(connection),
        {
            "PREPROCESS_REQUEST": 1800,
            "DELETE_REQUEST": 300,
            "PROJECT_DELETE_REQUEST": 300,
        },
    )

    await broker.consume(queue_name, limit=4)

    query, args = connection.fetched[0]
    assert query == "SELECT * FROM pgmq.read($1, $2, $3)"
    assert args == (queue_name, expected_vt, 4)


@pytest.mark.asyncio
async def test_pgmq_consume_preserves_queue_metadata() -> None:
    enqueued_at = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    connection = _FakeConnection(
        [
            {
                "msg_id": 42,
                "message": {"message_type": "PREPROCESS_REQUEST"},
                "enqueued_at": enqueued_at,
                "read_ct": 3,
            }
        ]
    )
    broker = PGMQBrokerClient(_FakePool(connection), {"PREPROCESS_REQUEST": 1800})

    messages = await broker.consume("PREPROCESS_REQUEST")

    assert len(messages) == 1
    assert messages[0].receipt_handle == "42"
    assert messages[0].enqueued_at == enqueued_at
    assert messages[0].read_ct == 3
