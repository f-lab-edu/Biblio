import pytest

from adapters.queue.inmemory_broker import InMemoryBrokerClient


@pytest.mark.asyncio
async def test_inmemory_broker_round_trip() -> None:
    broker = InMemoryBrokerClient()
    await broker.enqueue("PREPROCESS_REQUEST", {"message_type": "PREPROCESS_REQUEST"})

    messages = await broker.consume("PREPROCESS_REQUEST")

    assert len(messages) == 1
    await broker.ack("PREPROCESS_REQUEST", messages[0].receipt_handle)
    assert broker.acked_receipts == [f"PREPROCESS_REQUEST:{messages[0].receipt_handle}"]
