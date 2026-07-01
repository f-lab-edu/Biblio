import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.core.config import Settings
from src.core.dependencies import _build_broker_client
from src.infra.broker import BrokerPublishError, build_control_message, build_message
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
        video_ids=[video_id],
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
                    "payload_version": "v2",
                    "trace_id": str(trace_id),
                    "attempt": 1,
                    "issued_at": issued_at.isoformat(),
                    "video_ids": [str(video_id)],
                }
            ),
        )
    ]


@pytest.mark.asyncio
async def test_pgmq_broker_publishes_control_message_without_video_id() -> None:
    trace_id = uuid4()
    issued_at = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    message = build_control_message(
        "TRAINING_REQUEST",
        trace_id=trace_id,
        issued_at=issued_at,
    )
    connection = FakePGMQConnection(message_id=8)
    client = PGMQBrokerClient(connection)

    message_id = await client.publish(message)

    assert message_id == 8
    assert connection.calls == [
        (
            "SELECT pgmq.send(queue_name => $1, msg => $2::jsonb)",
            "TRAINING_REQUEST",
            json.dumps(
                {
                    "message_type": "TRAINING_REQUEST",
                    "payload_version": "v1",
                    "trace_id": str(trace_id),
                    "attempt": 1,
                    "issued_at": issued_at.isoformat(),
                }
            ),
        )
    ]


@pytest.mark.asyncio
async def test_pgmq_broker_publishes_project_delete_request_with_project_id() -> None:
    project_id = uuid4()
    trace_id = uuid4()
    issued_at = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    message = build_message(
        "PROJECT_DELETE_REQUEST",
        project_id=project_id,
        trace_id=trace_id,
        issued_at=issued_at,
    )
    connection = FakePGMQConnection(message_id=10)
    client = PGMQBrokerClient(connection)

    message_id = await client.publish(message)

    assert message_id == 10
    assert connection.calls == [
        (
            "SELECT pgmq.send(queue_name => $1, msg => $2::jsonb)",
            "PROJECT_DELETE_REQUEST",
            json.dumps(
                {
                    "message_type": "PROJECT_DELETE_REQUEST",
                    "payload_version": "v2",
                    "trace_id": str(trace_id),
                    "attempt": 1,
                    "issued_at": issued_at.isoformat(),
                    "project_id": str(project_id),
                }
            ),
        )
    ]


@pytest.mark.asyncio
async def test_pgmq_broker_publishes_rollback_to_dedicated_queue_with_expected_fields() -> None:
    trace_id = uuid4()
    issued_at = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    switched_at = datetime(2026, 3, 11, 9, 30, tzinfo=UTC)
    message = build_control_message(
        "ROLLBACK_REQUEST",
        trace_id=trace_id,
        issued_at=issued_at,
        queue_name="feedback.rollback.high",
        expected_active_model_version="2026.03.10",
        expected_switched_at=switched_at,
    )
    connection = FakePGMQConnection(message_id=9)
    client = PGMQBrokerClient(connection)

    message_id = await client.publish(message)

    assert message_id == 9
    assert connection.calls == [
        (
            "SELECT pgmq.send(queue_name => $1, msg => $2::jsonb)",
            "feedback.rollback.high",
            json.dumps(
                {
                    "message_type": "ROLLBACK_REQUEST",
                    "payload_version": "v1",
                    "trace_id": str(trace_id),
                    "attempt": 1,
                    "issued_at": issued_at.isoformat(),
                    "expected_active_model_version": "2026.03.10",
                    "expected_switched_at": switched_at.isoformat(),
                }
            ),
        )
    ]


def test_control_message_payload_omits_expected_fields_when_absent() -> None:
    message = build_control_message("TRAINING_REQUEST", trace_id=uuid4())

    payload = message.to_payload()

    assert "expected_active_model_version" not in payload
    assert "expected_switched_at" not in payload


@pytest.mark.asyncio
async def test_inmemory_broker_records_target_queue_with_payload() -> None:
    broker = InMemoryBrokerClient()
    message = build_control_message(
        "ROLLBACK_REQUEST",
        trace_id=uuid4(),
        issued_at=datetime(2026, 3, 12, 12, 0, tzinfo=UTC),
        queue_name="feedback.rollback.high",
    )

    await broker.publish(message)

    assert broker.published_envelopes == [
        ("feedback.rollback.high", message.to_payload())
    ]


@pytest.mark.asyncio
async def test_inmemory_broker_records_message_type_queue_when_unset() -> None:
    broker = InMemoryBrokerClient()
    message = build_message("PREPROCESS_REQUEST", video_ids=[uuid4()], trace_id=uuid4())

    await broker.publish(message)

    assert broker.published_envelopes[0][0] == "PREPROCESS_REQUEST"


@pytest.mark.asyncio
async def test_publish_with_retry_retries_then_succeeds() -> None:
    broker = InMemoryBrokerClient(failures_before_success=2)
    message = build_message(
        "DELETE_REQUEST",
        video_ids=[uuid4()],
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
        video_ids=[uuid4()],
        trace_id=uuid4(),
    )

    with pytest.raises(BrokerPublishError):
        await broker.publish_with_retry(message, max_attempts=3)

    assert broker.publish_attempts == 3
    assert broker.published_messages == []


def test_build_broker_client_converts_asyncpg_sqlalchemy_dsn_for_pgmq() -> None:
    settings = Settings(
        GCP_PROJECT_ID="test-project",
        GCS_VIDEO_BUCKET_NAME="test-bucket",
        JWT_SECRET_KEY="test-secret",
        DATABASE_URL="postgresql+asyncpg://user:pass@localhost:5432/app",
        BROKER_TYPE="pgmq",
    )

    broker = _build_broker_client(settings)

    assert isinstance(broker, PGMQBrokerClient)
    assert broker._dsn == "postgresql://user:pass@localhost:5432/app"
