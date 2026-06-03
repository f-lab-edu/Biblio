from dataclasses import dataclass
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.trace import TraceIdMiddleware
from src.infra.db.search_repository import (
    ServingSearchTarget,
    ServingSearchTargets,
)


@dataclass
class _FakeContainer:
    serving_target_provider: object | None


def _targets() -> ServingSearchTargets:
    return ServingSearchTargets(
        active=ServingSearchTarget(
            model_version="embedding-v2",
            index_name="active-index-v2",
        ),
        previous=ServingSearchTarget(
            model_version="embedding-v1",
            index_name="previous-index-v1",
        ),
    )


async def test_reload_serving_targets_returns_reloaded_targets() -> None:
    from src.api.v1.routers.internal import router

    provider = AsyncMock()
    provider.reload.return_value = _targets()

    app = FastAPI()
    app.state.container = _FakeContainer(serving_target_provider=provider)
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/internal/reload-serving-targets",
            json={"trace_id": "trace-body"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "active": {
            "model_version": "embedding-v2",
            "index_name": "active-index-v2",
        },
        "previous": {
            "model_version": "embedding-v1",
            "index_name": "previous-index-v1",
        },
    }
    provider.reload.assert_awaited_once()


async def test_reload_serving_targets_returns_503_when_provider_missing() -> None:
    from src.api.v1.routers.internal import router

    app = FastAPI()
    app.state.container = _FakeContainer(serving_target_provider=None)
    register_exception_handlers(app)
    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://testserver",
    ) as client:
        response = await client.post(
            "/internal/reload-serving-targets",
            json={},
        )

    assert response.status_code == 503
