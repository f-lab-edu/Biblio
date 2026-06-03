import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.infra.inmemory_broker import InMemoryBrokerClient
from src.middlewares.error_handler import ApiError, ConflictError
from src.services.admin_service import AdminService

ROLLBACK_QUEUE = "feedback.rollback.high"


def build_release(
    *,
    release_status: str = "STABLE",
    active_model_version: str | None = "embedding-v2",
    switched_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        release_status=release_status,
        active_model_version=active_model_version,
        active_index_name="project-index-active",
        switched_at=switched_at or datetime(2026, 3, 11, 9, 30, tzinfo=UTC),
    )


class FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def first(self):
        # Used by has_previous_stable_snapshot which calls result.first()
        return self._row


class FakeSession:
    def __init__(self, release_row, snapshot_row=None) -> None:
        self._release_row = release_row
        self._snapshot_row = snapshot_row
        self._call_count = 0

    async def execute(self, statement):
        # Async to satisfy the SQLAlchemy AsyncSession surface used by the repository.
        await asyncio.sleep(0)
        self._call_count += 1
        # First call: get_current (ModelRelease), second call: has_previous_stable_snapshot (ModelSnapshot)
        if self._call_count == 1:
            return FakeResult(self._release_row)
        return FakeResult(self._snapshot_row)


class FakeSessionContext:
    def __init__(self, release_row, snapshot_row=None) -> None:
        self._session = FakeSession(release_row, snapshot_row)

    async def __aenter__(self):
        # Async to satisfy the async session context manager surface.
        return self._session

    async def __aexit__(self, exc_type, exc, traceback):
        # Async to satisfy the async session context manager surface.
        return False


class FakeSessionFactory:
    def __init__(self, release_row, snapshot_row=None) -> None:
        self._release_row = release_row
        self._snapshot_row = snapshot_row

    def __call__(self):
        return FakeSessionContext(self._release_row, self._snapshot_row)


def build_service(
    release_row,
    broker: InMemoryBrokerClient | None = None,
    *,
    snapshot_row=None,
) -> AdminService:
    return AdminService(
        db_session_factory=FakeSessionFactory(release_row, snapshot_row),
        broker_client=broker or InMemoryBrokerClient(),
    )


# sentinel object to indicate a PREVIOUS_STABLE snapshot exists
_SNAPSHOT_ROW = object()


@pytest.mark.asyncio
async def test_trigger_rollback_publishes_request_to_rollback_queue() -> None:
    trace_id = uuid4()
    release = build_release()
    broker = InMemoryBrokerClient()
    service = build_service(release, broker, snapshot_row=_SNAPSHOT_ROW)

    result = await service.trigger_rollback(
        trace_id=trace_id,
        rollback_queue_name=ROLLBACK_QUEUE,
    )

    assert result == {"rollback_requested": True}
    assert len(broker.published_envelopes) == 1
    queue_name, payload = broker.published_envelopes[0]
    assert queue_name == ROLLBACK_QUEUE
    assert payload["message_type"] == "ROLLBACK_REQUEST"
    assert payload["trace_id"] == str(trace_id)
    assert payload["expected_active_model_version"] == release.active_model_version
    assert payload["expected_switched_at"] == release.switched_at.isoformat()


@pytest.mark.asyncio
async def test_trigger_rollback_conflicts_when_release_missing() -> None:
    broker = InMemoryBrokerClient()
    service = build_service(None, broker)

    with pytest.raises(ConflictError):
        await service.trigger_rollback(trace_id=uuid4(), rollback_queue_name=ROLLBACK_QUEUE)

    assert broker.published_envelopes == []


@pytest.mark.asyncio
async def test_trigger_rollback_conflicts_when_release_not_stable() -> None:
    broker = InMemoryBrokerClient()
    service = build_service(build_release(release_status="CANDIDATE_REINDEXING"), broker)

    with pytest.raises(ConflictError):
        await service.trigger_rollback(trace_id=uuid4(), rollback_queue_name=ROLLBACK_QUEUE)

    assert broker.published_envelopes == []


@pytest.mark.asyncio
async def test_trigger_rollback_conflicts_when_no_previous_stable_snapshot() -> None:
    """Registry has no PREVIOUS_STABLE row -> rollback is rejected."""
    broker = InMemoryBrokerClient()
    # snapshot_row=None means has_previous_stable_snapshot returns False
    service = build_service(build_release(), broker, snapshot_row=None)

    with pytest.raises(ConflictError):
        await service.trigger_rollback(trace_id=uuid4(), rollback_queue_name=ROLLBACK_QUEUE)

    assert broker.published_envelopes == []


@pytest.mark.asyncio
async def test_trigger_rollback_succeeds_when_previous_stable_snapshot_exists() -> None:
    """Registry has a PREVIOUS_STABLE row -> rollback is allowed."""
    broker = InMemoryBrokerClient()
    release = build_release()
    service = build_service(release, broker, snapshot_row=_SNAPSHOT_ROW)

    result = await service.trigger_rollback(
        trace_id=uuid4(),
        rollback_queue_name=ROLLBACK_QUEUE,
    )

    assert result == {"rollback_requested": True}
    assert len(broker.published_envelopes) == 1


@pytest.mark.asyncio
async def test_trigger_rollback_conflicts_when_switched_at_missing() -> None:
    broker = InMemoryBrokerClient()
    release = build_release()
    release.switched_at = None
    service = build_service(release, broker)

    with pytest.raises(ConflictError):
        await service.trigger_rollback(trace_id=uuid4(), rollback_queue_name=ROLLBACK_QUEUE)

    assert broker.published_envelopes == []


@pytest.mark.asyncio
async def test_trigger_rollback_raises_api_error_when_publish_fails() -> None:
    broker = InMemoryBrokerClient(failures_before_success=99)
    service = build_service(build_release(), broker, snapshot_row=_SNAPSHOT_ROW)

    with pytest.raises(ApiError):
        await service.trigger_rollback(trace_id=uuid4(), rollback_queue_name=ROLLBACK_QUEUE)
