from collections.abc import Callable

import httpx
from fastapi import FastAPI

from src.core.model_state import ModelState
from src.core.settings import Settings
from src.services.inference_service import InferenceService


class _FakeReloader:
    def __init__(self, ready_model_versions: list[str]) -> None:
        self._ready_model_versions = ready_model_versions
        self.trace_ids: list[str | None] = []

    async def reload(self, trace_id: str | None):
        self.trace_ids.append(trace_id)
        return type(
            "ReloadResult",
            (),
            {"ready_model_versions": self._ready_model_versions},
        )()


class TestReloadModels:
    async def test_returns_ready_model_versions(
        self,
        app_factory: Callable[..., FastAPI],
        settings_factory: Callable[..., Settings],
        ready_model_state_factory: Callable[[str], ModelState],
        inference_service_factory: Callable[..., InferenceService],
    ):
        settings = settings_factory()
        model_state = ready_model_state_factory()
        fake_reloader = _FakeReloader(["active-v2", "previous-v1"])
        app = app_factory(
            settings=settings,
            model_state=model_state,
            inference_service=inference_service_factory(
                settings=settings,
                model_state=model_state,
            ),
            model_reloader=fake_reloader,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="https://test",
        ) as client:
            response = await client.post(
                "/internal/reload-models",
                json={"trace_id": "trace-body"},
            )

        assert response.status_code == 200
        assert response.json() == {
            "ready_model_versions": ["active-v2", "previous-v1"]
        }
        assert fake_reloader.trace_ids == ["trace-body"]

    async def test_returns_503_when_reloader_is_missing(
        self,
        ready_client: httpx.AsyncClient,
    ):
        response = await ready_client.post(
            "/internal/reload-models",
            json={"trace_id": "trace-body"},
        )

        assert response.status_code == 503
