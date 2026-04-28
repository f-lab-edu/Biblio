import asyncio
import os
from datetime import UTC, datetime
from uuid import UUID

from src.infra.feedback_delivery import HttpFeedbackEventDeliveryClient
from src.schemas.feedback_dto import FeedbackEvent, FeedbackRating


def _uuid(value: str) -> UUID:
    return UUID(value)


def build_smoke_event() -> FeedbackEvent:
    return FeedbackEvent(
        event_id=_uuid("11111111-1111-4111-8111-111111111111"),
        user_id=_uuid("22222222-2222-4222-8222-222222222222"),
        project_id=_uuid("33333333-3333-4333-8333-333333333333"),
        req_id=_uuid("44444444-4444-4444-8444-444444444444"),
        query_text="local smoke query text",
        rating=FeedbackRating.LIKE,
        topk_ids=[_uuid("55555555-5555-4555-8555-555555555555")],
        used_ids=[_uuid("55555555-5555-4555-8555-555555555555")],
        active_model_version="embedding-smoke-v1",
        active_index_name="project-smoke-active",
        response_snapshot_ref="search_response_snapshot:44444444-4444-4444-8444-444444444444",
        created_at=datetime(2026, 4, 27, 12, 0, tzinfo=UTC),
        trace_id=_uuid("66666666-6666-4666-8666-666666666666"),
    )


async def main() -> None:
    endpoint_url = os.environ["FIP_FEEDBACK_DELIVERY_URL"]
    timeout_seconds = float(os.environ.get("FEEDBACK_DELIVERY_TIMEOUT_SECONDS", "2.0"))
    client = HttpFeedbackEventDeliveryClient(
        endpoint_url=endpoint_url,
        timeout_seconds=timeout_seconds,
    )
    await client.deliver(build_smoke_event())


if __name__ == "__main__":
    asyncio.run(main())
