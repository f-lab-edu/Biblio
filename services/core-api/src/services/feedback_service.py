from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from src.common.metrics import MetricsRecorder, NoopMetricsRecorder
from src.infra.db.feedback_repository import FeedbackRepository, SnapshotProjection
from src.infra.feedback_delivery import FeedbackEventDeliveryClient, FeedbackEventDeliveryError
from src.middlewares.error_handler import ApiError, ForbiddenError, NotFoundError
from src.schemas.feedback_dto import FeedbackEvent, FeedbackRequest, ServedVectorPath

FEEDBACK_EVENT_ID_NAMESPACE = UUID("9f8c4372-f213-52e3-92ce-b79d08fd15bf")


class FeedbackService:
    def __init__(
        self,
        *,
        db_session_factory: Any,
        delivery_client: FeedbackEventDeliveryClient,
        delivery_max_attempts: int = 3,
        delivery_retry_delay_seconds: float = 0.0,
        metrics_recorder: MetricsRecorder | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._delivery_client = delivery_client
        self._delivery_max_attempts = delivery_max_attempts
        self._delivery_retry_delay_seconds = delivery_retry_delay_seconds
        self._metrics_recorder = metrics_recorder or NoopMetricsRecorder()

    async def record_request(
        self,
        request: FeedbackRequest,
        *,
        requester_user_id: UUID,
        trace_id: UUID,
    ) -> None:
        # snapshot 조회
        async with self._db_session_factory() as session:
            repository = FeedbackRepository(session)
            snapshot = await repository.get_snapshot(request.req_id)

        if snapshot is None:
            raise NotFoundError("Search response snapshot was not found.")
        if snapshot.user_id != requester_user_id:
            raise ForbiddenError("Search response snapshot belongs to another user.")
        if snapshot.expires_at <= datetime.now(UTC):
            raise NotFoundError("Search response snapshot has expired.")

        event = _build_feedback_event(request, snapshot=snapshot, trace_id=trace_id)
        try:
            await self._delivery_client.deliver_with_retry(
                event,
                max_attempts=self._delivery_max_attempts,
                retry_delay_seconds=self._delivery_retry_delay_seconds,
            )
        except FeedbackEventDeliveryError as exc:
            self._metrics_recorder.increment_counter(
                "feedback_delivery_fail_count",
                tags={"dependency": "fip"},
            )
            raise ApiError("Feedback delivery failed after retries.") from exc


def _build_feedback_event(
    request: FeedbackRequest,
    *,
    snapshot: SnapshotProjection,
    trace_id: UUID,
) -> FeedbackEvent:
    return FeedbackEvent(
        event_id=_build_feedback_event_id(
            user_id=snapshot.user_id,
            req_id=snapshot.req_id,
            rating=request.rating,
        ),
        user_id=snapshot.user_id,
        project_id=snapshot.project_id,
        req_id=snapshot.req_id,
        query_text=snapshot.query_text,
        rating=request.rating,
        topk_ids=[UUID(value) for value in snapshot.topk_chunk_ids],
        used_ids=[UUID(value) for value in snapshot.used_chunk_ids],
        active_model_version=snapshot.active_model_version,
        active_index_name=snapshot.active_index_name,
        response_snapshot_ref=f"search_response_snapshot:{snapshot.req_id}",
        created_at=datetime.now(UTC),
        trace_id=trace_id,
        served_vector_paths=[
            ServedVectorPath.model_validate(path)
            for path in snapshot.served_vector_paths
        ],
    )


def _build_feedback_event_id(*, user_id: UUID, req_id: UUID, rating: str) -> UUID:
    canonical_name = f"feedback:{user_id}:{req_id}:{rating}"
    return uuid5(FEEDBACK_EVENT_ID_NAMESPACE, canonical_name)
