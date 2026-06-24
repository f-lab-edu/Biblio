from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from src.runtime.consumer import QueueMessageConsumer
from src.runtime.queue import BrokerMessage, InMemoryBrokerClient


@dataclass
class _HandledMessage:
    payload: dict[str, Any]


async def test_consumer_dispatches_one_message_and_acks_receipt() -> None:
    broker = InMemoryBrokerClient()
    handled: list[_HandledMessage] = []

    def handle_dataset(payload: dict[str, Any]) -> None:
        handled.append(_HandledMessage(payload=payload))

    consumer = QueueMessageConsumer(
        handlers={"DATASET_GENERATION_REQUEST": handle_dataset}
    )
    payload = {
        "message_type": "DATASET_GENERATION_REQUEST",
        "payload_version": "v1",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "issued_at": "2026-05-29T03:00:00+00:00",
    }
    await broker.enqueue("feedback.dataset", payload)

    processed = await consumer.run_once(broker, "feedback.dataset")

    assert processed is True
    assert handled == [_HandledMessage(payload=payload)]
    assert broker.acked_receipts == ["feedback.dataset:0"]


async def test_consumer_returns_false_when_queue_is_empty() -> None:
    consumer = QueueMessageConsumer(handlers={})

    processed = await consumer.run_once(InMemoryBrokerClient(), "feedback.dataset")

    assert processed is False


async def test_inmemory_broker_respects_consume_limit() -> None:
    broker = InMemoryBrokerClient()
    await broker.enqueue("feedback.dataset", {"message_type": "one"})
    await broker.enqueue("feedback.dataset", {"message_type": "two"})

    messages = await broker.consume("feedback.dataset", limit=1)

    assert messages == [BrokerMessage(receipt_handle="0", payload={"message_type": "one"})]
    assert broker.pending_count("feedback.dataset") == 1
