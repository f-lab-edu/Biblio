from uuid import uuid4

import pytest

from src.infra.queue.consumer import MessageDispatchError, PipelineWorkerConsumer
from src.schemas import MessageType


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
