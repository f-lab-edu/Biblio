from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.infra.feedback_delivery import (
    FeedbackEventDeliveryClient,
    FeedbackEventDeliveryError,
    HttpFeedbackEventDeliveryClient,
    InMemoryFeedbackEventDeliveryClient,
    RetriableFeedbackEventDeliveryError,
    TerminalFeedbackEventDeliveryError,
)
from src.schemas.feedback_dto import FeedbackEvent, FeedbackRating

def build_feedback_event() -> FeedbackEvent:
    return FeedbackEvent(
        event_id=uuid4(),
        user_id=uuid4(),
        project_id=uuid4(),
        req_id=uuid4(),
        query_text="What does the transcript say?",
        rating=FeedbackRating.LIKE,
        topk_ids=[uuid4()],
        used_ids=[uuid4()],
        active_model_version="embedding-v1",
        active_index_name="project-index-active",
        response_snapshot_ref="snapshot:test",
        created_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        trace_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_inmemory_client_captures_json_payload() -> None:
    event = build_feedback_event()
    client = InMemoryFeedbackEventDeliveryClient()

    await client.deliver(event)

    assert client.delivery_attempts == 1
    assert client.delivered_events == [event.model_dump(mode="json")]
    assert client.delivered_events[0]["rating"] == "LIKE"


@pytest.mark.asyncio
async def test_retries_then_succeeds() -> None:
    event = build_feedback_event()
    client = InMemoryFeedbackEventDeliveryClient(failures_before_success=2)

    await client.deliver_with_retry(event, max_attempts=3)

    assert client.delivery_attempts == 3
    assert client.delivered_events == [event.model_dump(mode="json")]


@pytest.mark.asyncio
async def test_raises_after_exhausting_attempts() -> None:
    event = build_feedback_event()
    client = InMemoryFeedbackEventDeliveryClient(failures_before_success=2)

    with pytest.raises(FeedbackEventDeliveryError):
        await client.deliver_with_retry(event, max_attempts=2)

    assert client.delivery_attempts == 2
    assert client.delivered_events == []


@pytest.mark.asyncio
async def test_reuses_same_event_object_across_attempts() -> None:
    event = build_feedback_event()
    client = RecordingRetryDeliveryClient(failures_before_success=2)

    await client.deliver_with_retry(event, max_attempts=3)

    assert client.delivery_attempts == 3
    assert client.event_object_ids == [id(event), id(event), id(event)]


@pytest.mark.asyncio
async def test_does_not_retry_terminal_error() -> None:
    event = build_feedback_event()
    client = TerminalFailureDeliveryClient()

    with pytest.raises(TerminalFeedbackEventDeliveryError):
        await client.deliver_with_retry(event, max_attempts=3)

    assert client.delivery_attempts == 1


@pytest.mark.asyncio
async def test_http_client_posts_json_payload() -> None:
    sent_requests = []

    def send_request(request, timeout_seconds):
        sent_requests.append((request, timeout_seconds))
        return 202

    event = build_feedback_event()
    client = HttpFeedbackEventDeliveryClient(
        endpoint_url="https://feedback-ingestion-pipeline:8080/feedback/events",
        timeout_seconds=2.5,
        send_request=send_request,
    )

    await client.deliver(event)

    assert len(sent_requests) == 1
    request, timeout_seconds = sent_requests[0]
    assert request.full_url == "https://feedback-ingestion-pipeline:8080/feedback/events"
    assert request.method == "POST"
    assert request.headers["Content-type"] == "application/json"
    assert timeout_seconds == pytest.approx(2.5)
    assert b'"schema_version":1' in request.data


@pytest.mark.asyncio
async def test_http_client_raises_for_rejected_status() -> None:
    def send_request(request, timeout_seconds):
        return 503

    client = HttpFeedbackEventDeliveryClient(
        endpoint_url="https://feedback-ingestion-pipeline:8080/feedback/events",
        send_request=send_request,
    )

    with pytest.raises(RetriableFeedbackEventDeliveryError):
        await client.deliver(build_feedback_event())


@pytest.mark.asyncio
async def test_http_client_maps_terminal_rejection_to_non_retryable_error() -> None:
    def send_request(request, timeout_seconds):
        return 400

    client = HttpFeedbackEventDeliveryClient(
        endpoint_url="https://feedback-ingestion-pipeline:8080/feedback/events",
        send_request=send_request,
    )

    with pytest.raises(TerminalFeedbackEventDeliveryError):
        await client.deliver(build_feedback_event())


@pytest.mark.asyncio
async def test_http_client_attaches_bearer_token_with_service_audience() -> None:
    sent_requests = []
    seen_audiences = []

    def send_request(request, timeout_seconds):
        sent_requests.append(request)
        return 202

    def id_token_provider(audience):
        seen_audiences.append(audience)
        return "id-token-123"

    client = HttpFeedbackEventDeliveryClient(
        endpoint_url="https://feedback-ingestion-pipeline-xyz.run.app/feedback/events",
        send_request=send_request,
        id_token_provider=id_token_provider,
    )

    await client.deliver(build_feedback_event())

    # audience는 경로를 뺀 서비스 주소여야 한다.
    assert seen_audiences == ["https://feedback-ingestion-pipeline-xyz.run.app"]
    assert sent_requests[0].get_header("Authorization") == "Bearer id-token-123"


@pytest.mark.asyncio
async def test_http_client_omits_authorization_without_token_provider() -> None:
    sent_requests = []

    def send_request(request, timeout_seconds):
        sent_requests.append(request)
        return 202

    client = HttpFeedbackEventDeliveryClient(
        endpoint_url="https://feedback-ingestion-pipeline:8080/feedback/events",
        send_request=send_request,
    )

    await client.deliver(build_feedback_event())

    assert sent_requests[0].get_header("Authorization") is None


class RecordingRetryDeliveryClient(FeedbackEventDeliveryClient):
    def __init__(self, *, failures_before_success: int) -> None:
        self._failures_before_success = failures_before_success
        self.delivery_attempts = 0
        self.event_object_ids: list[int] = []

    async def deliver(self, event: FeedbackEvent) -> None:
        # Async to satisfy the production delivery interface used by retry tests.
        self.delivery_attempts += 1
        self.event_object_ids.append(id(event))
        if self._failures_before_success > 0:
            self._failures_before_success -= 1
            raise RetriableFeedbackEventDeliveryError(
                "Simulated transient delivery failure."
            )


class TerminalFailureDeliveryClient(FeedbackEventDeliveryClient):
    def __init__(self) -> None:
        self.delivery_attempts = 0

    async def deliver(self, event: FeedbackEvent) -> None:
        # Async to satisfy the production delivery interface used by retry tests.
        self.delivery_attempts += 1
        raise TerminalFeedbackEventDeliveryError("Simulated terminal delivery failure.")
