import asyncio
"""Router wiring smoke tests for the admin-ops foundation routers.

Goal: prove that `feedbacks` and `admin` routers are included under the
existing `/api/v1` prefix and reject invalid or unauthorized requests.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from src.infra.feedback_delivery import FeedbackEventDeliveryError
from src.models.admin_ops import Project, SearchResponseSnapshot
from src.schemas.feedback_dto import FeedbackEvent
from tests.support import AppContext, auth_headers, create_token


def _route_paths(app_context: AppContext) -> set[str]:
    return {route.path for route in app_context.app.routes if hasattr(route, "path")}


@pytest.mark.asyncio
async def test_feedbacks_and_admin_routes_are_wired(app_context: AppContext) -> None:
    paths = _route_paths(app_context)
    assert "/api/v1/feedbacks" in paths
    assert "/api/v1/admin/ml-pipeline-runs/retrigger" in paths
    assert "/api/v1/admin/model-release/rollback" in paths


@pytest.mark.asyncio
async def test_feedbacks_endpoint_requires_auth(api_client: AsyncClient) -> None:
    response = await api_client.post("/api/v1/feedbacks", json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_admin_endpoints_require_auth(api_client: AsyncClient) -> None:
    training_response = await api_client.post("/api/v1/admin/ml-pipeline-runs/retrigger")
    rollback_response = await api_client.post("/api/v1/admin/model-release/rollback")
    assert training_response.status_code == 401
    assert rollback_response.status_code == 401


@pytest.mark.asyncio
async def test_feedback_endpoint_returns_not_found_for_missing_snapshot(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    payload = {
        "req_id": str(uuid4()),
        "rating": "LIKE",
    }

    response = await api_client.post(
        "/api/v1/feedbacks",
        json=payload,
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_feedback_endpoint_rejects_internal_event_fields(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    payload = {
        "req_id": str(uuid4()),
        "rating": "LIKE",
        "user_id": str(requester_user_id),
        "project_id": str(uuid4()),
    }

    response = await api_client.post(
        "/api/v1/feedbacks",
        json=payload,
        headers=auth_headers(token),
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_feedback_endpoint_increments_metric_on_delivery_failure(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    req_id = uuid4()
    project_id = uuid4()
    metrics = RecordingMetrics()

    app_context.app.state.container.metrics_recorder = metrics
    app_context.app.state.container.feedback_delivery_client = AlwaysFailFeedbackDeliveryClient()
    await _seed_feedback_snapshot(
        app_context,
        requester_user_id=requester_user_id,
        project_id=project_id,
        req_id=req_id,
    )

    response = await api_client.post(
        "/api/v1/feedbacks",
        json={"req_id": str(req_id), "rating": "LIKE"},
        headers=auth_headers(token),
    )

    assert response.status_code == 500
    assert metrics.counters == [
        (
            "feedback_delivery_fail_count",
            {"dependency": "fip"},
        )
    ]


@pytest.mark.asyncio
async def test_admin_training_returns_not_implemented_for_skeleton(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = UUID(str(uuid4()))
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))

    response = await api_client.post(
        "/api/v1/admin/ml-pipeline-runs/retrigger",
        headers=auth_headers(token),
    )

    assert response.status_code == 501


async def _seed_feedback_snapshot(
    app_context: AppContext,
    *,
    requester_user_id: UUID,
    project_id: UUID,
    req_id: UUID,
) -> None:
    async with app_context.session_factory() as session:
        session.add(
            Project(
                id=project_id,
                user_id=requester_user_id,
                title="Feedback project",
            )
        )
        session.add(
            SearchResponseSnapshot(
                req_id=req_id,
                user_id=requester_user_id,
                project_id=project_id,
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
                project_serving_state="SERVABLE",
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await session.commit()


class RecordingMetrics:
    def __init__(self) -> None:
        self.counters: list[tuple[str, dict[str, str]]] = []

    def increment_counter(
        self,
        name: str,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.counters.append((name, dict(tags or {})))


class AlwaysFailFeedbackDeliveryClient:
    async def deliver(self, event: FeedbackEvent) -> None:
        # Async to satisfy the production delivery interface used by the router.
        await asyncio.sleep(0)
        raise FeedbackEventDeliveryError("Simulated delivery failure.")

    async def deliver_with_retry(
        self,
        event: FeedbackEvent,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.0,
    ) -> None:
        # Async to satisfy the production delivery interface used by the router.
        _ = max_attempts, retry_delay_seconds
        await asyncio.sleep(0)
        raise FeedbackEventDeliveryError("Simulated delivery failure.")
