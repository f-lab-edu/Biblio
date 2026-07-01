import asyncio
from uuid import uuid4

import pytest

from src.infra.db.video_repository import VideoRecord
from src.infra.queue.consumer import PipelineWorkerConsumer
from src.infra.queue.inmemory_broker import InMemoryBrokerClient
from src.config.settings import Settings
from src.main import build_consumer_bootstrap
from src.schemas import MessageType


@pytest.mark.asyncio
async def test_consumer_flow_dispatches_and_acks(
    video_repository,
    process_video_use_case,
    delete_video_use_case,
    storage_client,
) -> None:
    broker = InMemoryBrokerClient()
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    consumer = PipelineWorkerConsumer(
        {
            MessageType.PREPROCESS_REQUEST: lambda envelope: process_video_use_case.execute(
                video_id=str(envelope.video_ids[0]),
                trace_id=str(envelope.trace_id),
            ),
            MessageType.DELETE_REQUEST: lambda envelope: delete_video_use_case.execute(
                video_ids=[str(video_id) for video_id in envelope.video_ids],
                trace_id=str(envelope.trace_id),
            ),
        }
    )
    await broker.enqueue(
        "PREPROCESS_REQUEST",
        {
            "message_type": "PREPROCESS_REQUEST",
            "payload_version": "v2",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "video_ids": [video_id],
            "issued_at": "2024-01-01T00:00:00Z",
        },
    )

    bootstrap = build_consumer_bootstrap(broker=broker, consumer=consumer, queue_names=["PREPROCESS_REQUEST"])
    await bootstrap(Settings(
        BROKER_TYPE="inmemory",
        DATABASE_URL="sqlite",
        GCP_PROJECT_ID="gcp",
        GCS_VIDEO_BUCKET_NAME="bucket",
        EMBEDDING_API_URL="https://embedding.local/embed",
    ))

    assert len(broker.acked_receipts) == 1


@pytest.mark.asyncio
async def test_consumer_flow_processes_multiple_messages_from_same_queue_concurrently() -> None:
    broker = InMemoryBrokerClient()
    active = 0
    max_active = 0
    lock = asyncio.Lock()

    async def handler(_envelope) -> None:
        nonlocal active, max_active
        async with lock:
            active += 1
            max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        async with lock:
            active -= 1

    consumer = PipelineWorkerConsumer({MessageType.PREPROCESS_REQUEST: handler})

    for index in range(2):
        await broker.enqueue(
            "PREPROCESS_REQUEST",
            {
                "message_type": "PREPROCESS_REQUEST",
                "payload_version": "v2",
                "trace_id": str(uuid4()),
                "attempt": 1,
                "video_ids": [str(uuid4())],
                "issued_at": f"2024-01-01T00:00:0{index}Z",
            },
        )

    bootstrap = build_consumer_bootstrap(broker=broker, consumer=consumer, queue_names=["PREPROCESS_REQUEST"])
    await bootstrap(Settings(
        BROKER_TYPE="inmemory",
        DATABASE_URL="sqlite",
        GCP_PROJECT_ID="gcp",
        GCS_VIDEO_BUCKET_NAME="bucket",
        EMBEDDING_API_URL="https://embedding.local/embed",
        WORKER_CONCURRENCY=2,
    ))

    assert max_active == 2
    assert len(broker.acked_receipts) == 2
