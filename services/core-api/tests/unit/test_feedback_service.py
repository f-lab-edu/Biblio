import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.infra.feedback_delivery import InMemoryFeedbackEventDeliveryClient
from src.middlewares.error_handler import ApiError, ForbiddenError, NotFoundError
from src.schemas.feedback_dto import FeedbackRequest, FeedbackRating
from src.services.feedback_service import FeedbackService


def build_snapshot_row(
    *,
    user_id,
    expires_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        req_id=uuid4(),
        user_id=user_id,
        project_id=uuid4(),
        query_text="What changed in the active model?",
        topk_chunk_ids=[str(uuid4())],
        used_chunk_ids=[str(uuid4())],
        active_model_version="embedding-v1",
        active_index_name="project-index-active",
        served_vector_paths=[
            {
                "model_version": "embedding-v1",
                "index_name": "project-index-active",
            }
        ],
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_delivers_event_from_snapshot_context() -> None:
    requester_user_id = uuid4()
    trace_id = uuid4()
    snapshot = build_snapshot_row(user_id=requester_user_id)
    delivery_client = InMemoryFeedbackEventDeliveryClient()
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(snapshot),
        delivery_client=delivery_client,
    )

    await service.record_request(
        FeedbackRequest(req_id=snapshot.req_id, rating=FeedbackRating.LIKE),
        requester_user_id=requester_user_id,
        trace_id=trace_id,
    )

    assert delivery_client.delivery_attempts == 1
    delivered_event = delivery_client.delivered_events[0]
    assert delivered_event["req_id"] == str(snapshot.req_id)
    assert delivered_event["user_id"] == str(requester_user_id)
    assert delivered_event["project_id"] == str(snapshot.project_id)
    assert delivered_event["query_text"] == snapshot.query_text
    assert delivered_event["topk_ids"] == snapshot.topk_chunk_ids
    assert delivered_event["used_ids"] == snapshot.used_chunk_ids
    assert delivered_event["trace_id"] == str(trace_id)


@pytest.mark.asyncio
# 같은 피드백이면 같은 event id 사용 여부
async def test_reuses_event_id_for_same_input() -> None:
    requester_user_id = uuid4()
    snapshot = build_snapshot_row(user_id=requester_user_id)
    delivery_client = InMemoryFeedbackEventDeliveryClient()
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(snapshot),
        delivery_client=delivery_client,
    )

    request = FeedbackRequest(req_id=snapshot.req_id, rating=FeedbackRating.LIKE)

    await service.record_request(
        request,
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )
    await service.record_request(
        request,
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )

    delivered_event_ids = [
        UUID(delivered_event["event_id"])
        for delivered_event in delivery_client.delivered_events
    ]

    assert delivered_event_ids[0] == delivered_event_ids[1]


@pytest.mark.asyncio
# 같은 피드백 다른 평가면 envet_id도 달라지는지 테스트
async def test_uses_different_event_id_for_different_rating() -> None:
    requester_user_id = uuid4()
    snapshot = build_snapshot_row(user_id=requester_user_id)
    delivery_client = InMemoryFeedbackEventDeliveryClient()
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(snapshot),
        delivery_client=delivery_client,
    )

    await service.record_request(
        FeedbackRequest(req_id=snapshot.req_id, rating=FeedbackRating.LIKE),
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )
    await service.record_request(
        FeedbackRequest(req_id=snapshot.req_id, rating=FeedbackRating.DISLIKE),
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )

    delivered_event_ids = [
        UUID(delivered_event["event_id"])
        for delivered_event in delivery_client.delivered_events
    ]

    assert delivered_event_ids[0] != delivered_event_ids[1]


@pytest.mark.asyncio
async def test_rejects_missing_snapshot() -> None:
    delivery_client = InMemoryFeedbackEventDeliveryClient()
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(None),
        delivery_client=delivery_client,
    )

    with pytest.raises(NotFoundError):
        await service.record_request(
            FeedbackRequest(req_id=uuid4(), rating=FeedbackRating.DISLIKE),
            requester_user_id=uuid4(),
            trace_id=uuid4(),
        )

    assert delivery_client.delivery_attempts == 0


@pytest.mark.asyncio
async def test_rejects_foreign_snapshot() -> None:
    delivery_client = InMemoryFeedbackEventDeliveryClient()
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(build_snapshot_row(user_id=uuid4())),
        delivery_client=delivery_client,
    )

    with pytest.raises(ForbiddenError):
        await service.record_request(
            FeedbackRequest(req_id=uuid4(), rating=FeedbackRating.LIKE),
            requester_user_id=uuid4(),
            trace_id=uuid4(),
        )

    assert delivery_client.delivery_attempts == 0


@pytest.mark.asyncio
async def test_rejects_expired_snapshot() -> None:
    requester_user_id = uuid4()
    delivery_client = InMemoryFeedbackEventDeliveryClient()
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(
            build_snapshot_row(
                user_id=requester_user_id,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        ),
        delivery_client=delivery_client,
    )

    with pytest.raises(NotFoundError):
        await service.record_request(
            FeedbackRequest(req_id=uuid4(), rating=FeedbackRating.LIKE),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert delivery_client.delivery_attempts == 0


@pytest.mark.asyncio
async def test_translates_delivery_failure() -> None:
    requester_user_id = uuid4()
    delivery_client = InMemoryFeedbackEventDeliveryClient(failures_before_success=2)
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(build_snapshot_row(user_id=requester_user_id)),
        delivery_client=delivery_client,
        delivery_max_attempts=2,
    )

    with pytest.raises(ApiError):
        await service.record_request(
            FeedbackRequest(req_id=uuid4(), rating=FeedbackRating.DISLIKE),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert delivery_client.delivery_attempts == 2
    assert delivery_client.delivered_events == []


@pytest.mark.asyncio
async def test_increments_delivery_failure_metric() -> None:
    requester_user_id = uuid4()
    metrics = RecordingMetrics()
    delivery_client = InMemoryFeedbackEventDeliveryClient(failures_before_success=2)
    service = FeedbackService(
        db_session_factory=FakeSessionFactory(build_snapshot_row(user_id=requester_user_id)),
        delivery_client=delivery_client,
        delivery_max_attempts=2,
        metrics_recorder=metrics,
    )

    with pytest.raises(ApiError):
        await service.record_request(
            FeedbackRequest(req_id=uuid4(), rating=FeedbackRating.DISLIKE),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert metrics.counters == [
        (
            "feedback_delivery_fail_count",
            {"dependency": "fip"},
        )
    ]


class RecordingMetrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, str]]] = []

    def increment_counter(
        self,
        name: str,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        self.counters.append((name, dict(tags or {})))


class FakeResult:
    def __init__(self, row) -> None:
        self._row = row

    def one_or_none(self):
        return self._row


class FakeSession:
    def __init__(self, row) -> None:
        self._row = row

    async def execute(self, statement):
        # Async to satisfy the SQLAlchemy AsyncSession surface used by the repository.
        await asyncio.sleep(0)
        return FakeResult(self._row)


class FakeSessionContext:
    def __init__(self, row) -> None:
        self._session = FakeSession(row)

    async def __aenter__(self):
        # Async to satisfy the async session context manager surface.
        return self._session

    async def __aexit__(self, exc_type, exc, traceback):
        # Async to satisfy the async session context manager surface.
        return False


class FakeSessionFactory:
    def __init__(self, row) -> None:
        self._row = row

    def __call__(self):
        return FakeSessionContext(self._row)
