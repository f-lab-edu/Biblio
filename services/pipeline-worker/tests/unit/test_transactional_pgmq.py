import json

import pytest

from src.infra.queue.transactional_pgmq import TransactionalPGMQPublisher


class _ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _RecordingSession:
    def __init__(self, message_id: int) -> None:
        self._message_id = message_id
        self.statement = None
        self.parameters = None

    async def execute(self, statement, parameters):
        self.statement = statement
        self.parameters = parameters
        return _ScalarResult(self._message_id)


@pytest.mark.asyncio
async def test_send_uses_the_supplied_session_and_returns_message_id() -> None:
    session = _RecordingSession(message_id=42)
    publisher = TransactionalPGMQPublisher()
    payload = {"message_type": "TRANSCRIBE_PART", "attempt": 1}

    message_id = await publisher.send(session, "TRANSCRIBE_PART", payload)

    assert message_id == 42
    assert str(session.statement) == (
        "SELECT pgmq.send(:queue_name, CAST(:payload AS jsonb))"
    )
    assert session.parameters == {
        "queue_name": "TRANSCRIBE_PART",
        "payload": json.dumps(payload),
    }
