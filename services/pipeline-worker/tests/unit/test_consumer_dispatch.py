from datetime import UTC, datetime
from uuid import uuid4

import pytest
from loguru import logger

from src.infra.queue.consumer import MessageDispatchError, PipelineWorkerConsumer
from src.infra.queue.broker import BrokerMessage
from src.infra.queue.inmemory_broker import InMemoryBrokerClient
from src.schemas import MessageEnvelope, MessageType


@pytest.mark.asyncio
async def test_consumer_dispatches_handler() -> None:
    seen: list[str] = []

    def handler(envelope):
        seen.append(str(envelope.trace_id))

    consumer = PipelineWorkerConsumer({MessageType.PREPROCESS_REQUEST: handler})
    payload = {
        "message_type": MessageType.PREPROCESS_REQUEST.value,
        "payload_version": "v2",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "video_ids": [str(uuid4())],
        "issued_at": "2024-01-01T00:00:00Z",
    }

    envelope = await consumer.consume(payload)
    assert seen == [str(envelope.trace_id)]


@pytest.mark.asyncio
async def test_consumer_raises_without_handler() -> None:
    def noop(_):
        return None

    consumer = PipelineWorkerConsumer({MessageType.PREPROCESS_REQUEST: noop})
    payload = {
        "message_type": MessageType.DELETE_REQUEST.value,
        "payload_version": "v2",
        "trace_id": str(uuid4()),
        "attempt": 1,
        "video_ids": [str(uuid4())],
        "issued_at": "2024-01-01T00:00:00Z",
    }

    with pytest.raises(MessageDispatchError):
        await consumer.consume(payload)


@pytest.mark.asyncio
async def test_run_once_logs_dispatch_start() -> None:
    def noop(_):
        return None

    broker = InMemoryBrokerClient()
    consumer = PipelineWorkerConsumer({MessageType.PREPROCESS_REQUEST: noop})
    await broker.enqueue(
        "PREPROCESS_REQUEST",
        {
            "message_type": MessageType.PREPROCESS_REQUEST.value,
            "payload_version": "v2",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "video_ids": [str(uuid4())],
            "issued_at": "2026-08-13T11:59:59Z",
        },
    )
    records: list[dict] = []
    sink_id = logger.add(lambda log_message: records.append(log_message.record))
    try:
        processed = await consumer.run_once(broker, "PREPROCESS_REQUEST")
    finally:
        logger.remove(sink_id)

    assert processed is True
    assert any(
        record["message"].startswith("queue.message.started ") for record in records
    )


def test_consumer_logs_queue_wait_when_dispatch_starts() -> None:
    consumer = PipelineWorkerConsumer({})
    trace_id = uuid4()
    video_id = uuid4()
    envelope = MessageEnvelope.model_validate(
        {
            "message_type": MessageType.PREPROCESS_REQUEST.value,
            "payload_version": "v2",
            "trace_id": str(trace_id),
            "attempt": 2,
            "video_ids": [str(video_id)],
            "issued_at": "2026-08-13T11:59:59Z",
        }
    )
    message = BrokerMessage(
        receipt_handle="42",
        payload=envelope.model_dump(mode="json"),
        enqueued_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        read_ct=3,
    )
    started_at = datetime(2026, 8, 13, 12, 0, 2, 500_000, tzinfo=UTC)
    records: list[dict] = []
    sink_id = logger.add(lambda log_message: records.append(log_message.record))
    try:
        consumer._log_dispatch_started(
            envelope=envelope,
            message=message,
            started_at=started_at,
        )
    finally:
        logger.remove(sink_id)

    dispatch_record = next(
        record for record in records if record["message"].startswith("queue.message.started ")
    )
    assert dispatch_record["extra"]["trace_id"] == str(trace_id)
    assert dispatch_record["extra"]["video_id"] == str(video_id)
    assert dispatch_record["extra"]["attempt"] == 2
    assert dispatch_record["extra"]["read_ct"] == 3
    assert dispatch_record["extra"]["queue_wait_ms"] == pytest.approx(2500.0)
    assert dispatch_record["extra"]["issued_at"] == "2026-08-13T11:59:59+00:00"
    assert dispatch_record["extra"]["enqueued_at"] == "2026-08-13T12:00:00+00:00"
    assert dispatch_record["extra"]["started_at"] == "2026-08-13T12:00:02.500000+00:00"
    assert "queue_wait_ms=2500.0 attempt=2 read_ct=3" in dispatch_record["message"]
