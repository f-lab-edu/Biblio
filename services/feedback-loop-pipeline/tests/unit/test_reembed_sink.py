from uuid import uuid4
import pytest
from src.release.reembed_sink import BrokerReembeddingSink


class _RecordingBroker:
    def __init__(self):
        self.enqueued = []
    async def enqueue(self, queue_name, payload):
        self.enqueued.append((queue_name, payload))


@pytest.mark.asyncio
async def test_sink_publishes_reembedding_request():
    broker = _RecordingBroker()
    sink = BrokerReembeddingSink(broker=broker, queue_name="feedback.reembedding")
    await sink.request_reembedding(
        video_id=uuid4(), target_model_version="v1", target_index_name="index-v1"
    )
    assert len(broker.enqueued) == 1
    queue, payload = broker.enqueued[0]
    assert queue == "feedback.reembedding"
    assert payload["message_type"] == "REEMBEDDING_REQUEST"
    assert payload["target_index_name"] == "index-v1"
    assert payload["video_id"]
