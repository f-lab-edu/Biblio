from __future__ import annotations

from uuid import uuid4

import pytest

from adapters.db.video_repository import VideoRecord
from adapters.queue.consumer import PipelineWorkerConsumer
from adapters.queue.inmemory_broker import InMemoryBrokerClient
from main import build_consumer_bootstrap
from schemas import MessageType


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
                video_id=str(envelope.video_id),
                trace_id=str(envelope.trace_id),
                stt_model_version="google-stt-v1",
                embedding_model_version="v001",
            ),
            MessageType.DELETE_REQUEST: lambda envelope: delete_video_use_case.execute(
                video_id=str(envelope.video_id),
                trace_id=str(envelope.trace_id),
            ),
        }
    )
    await broker.enqueue(
        "PREPROCESS_REQUEST",
        {
            "message_type": "PREPROCESS_REQUEST",
            "payload_version": "v1",
            "trace_id": str(uuid4()),
            "attempt": 1,
            "video_id": video_id,
            "issued_at": "2024-01-01T00:00:00Z",
        },
    )

    bootstrap = build_consumer_bootstrap(broker=broker, consumer=consumer, queue_names=["PREPROCESS_REQUEST"])
    await bootstrap(__import__("config.settings", fromlist=["Settings"]).Settings(
        BROKER_TYPE="inmemory",
        DATABASE_URL="sqlite",
        GCP_PROJECT_ID="gcp",
        GCS_VIDEO_BUCKET_NAME="bucket",
        EMBEDDING_API_URL="http://embedding.local/embed",
    ))

    assert len(broker.acked_receipts) == 1
