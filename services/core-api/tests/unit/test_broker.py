from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.infra.broker import BrokerPublishError, build_message
from src.infra.inmemory_broker import InMemoryBrokerClient
from src.infra.pgmq_client import PGMQBrokerClient


class FakePGMQConnection:
    def __init__(self, *, message_id: int | None = 1) -> None:
        self.message_id = message_id
        self.calls: list[tuple[str, str, str]] = []

    async def fetchval(self, query: str, queue_name: str, payload_json: str) -> int | None:
        self.calls.append((query, queue_name, payload_json))
        return self.message_id


@pytest.mark.asyncio
async def test_pgmq_broker_publishes_spec_payload_to_matching_queue() -> None:
    video_id = uuid4()
    trace_id = uuid4()
    issued_at = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    message = build_message(
        "PREPROCESS_REQUEST",
        video_id=video_id,
        trace_id=trace_id,
        issued_at=issued_at,
    )
    connection = FakePGMQConnection(message_id=7)
    client = PGMQBrokerClient(connection)

    message_id = await client.publish(message)

    assert message_id == 7
    assert connection.calls == [
        (
            "SELECT pgmq.send(queue_name => $1, msg => $2::jsonb)",
            "PREPROCESS_REQUEST",
            json.dumps(
                {
                    "message_type": "PREPROCESS_REQUEST",
                    "payload_version": "v1",
                    "trace_id": str(trace_id),
                    "attempt": 1,
                    "video_id": str(video_id),
                    "issued_at": issued_at.isoformat(),
                }
            ),
        )
    ]


@pytest.mark.asyncio
async def test_publish_with_retry_retries_then_succeeds() -> None:
    broker = InMemoryBrokerClient(failures_before_success=2)
    message = build_message(
        "DELETE_REQUEST",
        video_id=uuid4(),
        trace_id=uuid4(),
        issued_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
    )

    message_id = await broker.publish_with_retry(message, max_attempts=3)

    assert message_id == 1
    assert broker.publish_attempts == 3
    assert broker.published_messages == [message.to_payload()]


@pytest.mark.asyncio
async def test_publish_with_retry_raises_after_exhausting_attempts() -> None:
    broker = InMemoryBrokerClient(failures_before_success=3)
    message = build_message(
        "PREPROCESS_REQUEST",
        video_id=uuid4(),
        trace_id=uuid4(),
    )

    with pytest.raises(BrokerPublishError):
        await broker.publish_with_retry(message, max_attempts=3)

    assert broker.publish_attempts == 3
    assert broker.published_messages == []
