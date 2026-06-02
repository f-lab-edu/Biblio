from __future__ import annotations

import json
from typing import Any

from src.runtime.queue import PGMQBrokerClient, to_asyncpg_dsn


class _FakeConnection:
    def __init__(self, fetch_rows: list[dict[str, Any]]) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self._fetch_rows = fetch_rows

    async def execute(self, query: str, *args: Any) -> None:
        self.executed.append((query, args))

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((query, args))
        return self._fetch_rows


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    def acquire(self) -> "_FakePool":
        return self

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


async def test_enqueue_serializes_payload_to_json_string_for_jsonb() -> None:
    connection = _FakeConnection(fetch_rows=[])
    broker = PGMQBrokerClient(_FakePool(connection))
    payload = {"type": "TRAINING_REQUEST", "smoke": True}

    await broker.enqueue("feedback.training", payload)

    query, args = connection.executed[0]
    assert "pgmq.send" in query
    queue_name, sent_payload = args
    assert queue_name == "feedback.training"
    # asyncpg encodes a jsonb parameter from a JSON string, never a raw dict.
    assert isinstance(sent_payload, str)
    assert json.loads(sent_payload) == payload


async def test_consume_normalizes_jsonb_string_message() -> None:
    rows = [{"msg_id": 7, "message": json.dumps({"type": "TRAINING_REQUEST"})}]
    broker = PGMQBrokerClient(_FakePool(_FakeConnection(fetch_rows=rows)))

    messages = await broker.consume("feedback.training", limit=1)

    assert len(messages) == 1
    assert messages[0].receipt_handle == "7"
    assert messages[0].payload == {"type": "TRAINING_REQUEST"}


async def test_ack_archives_receipt_as_bigint() -> None:
    connection = _FakeConnection(fetch_rows=[])
    broker = PGMQBrokerClient(_FakePool(connection))

    await broker.ack("feedback.training", "7")

    query, args = connection.executed[0]
    assert "pgmq.archive" in query
    assert args == ("feedback.training", 7)


def test_to_asyncpg_dsn_strips_sqlalchemy_driver_suffix() -> None:
    dsn = to_asyncpg_dsn("postgresql+asyncpg://u:p@h:5432/db")

    assert dsn == "postgresql://u:p@h:5432/db"
