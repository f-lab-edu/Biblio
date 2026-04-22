"""Router wiring smoke tests for the admin-ops foundation routers.

Goal: prove that `feedbacks` and `admin` routers are included under the
existing `/api/v1` prefix and that their skeleton endpoints reject anonymous
callers as expected. Business behavior lives in the follow-up branches.
"""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

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
async def test_feedback_endpoint_returns_not_implemented_for_skeleton(
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

    assert response.status_code == 501


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

    assert response.status_code == 422


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
