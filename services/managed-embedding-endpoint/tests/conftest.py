import threading
from collections.abc import AsyncIterator, Callable

import httpx
import pytest
from fastapi import FastAPI

from src.core.model_state import ModelState
from src.core.settings import Settings
from src.main import create_app
from src.services.inference_service import InferenceService

EMBEDDING_DIM = 4


class StubRuntime:
    """Deterministic runtime: embedding[i] = [float(len(text))] * dim."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t))] * self._dim for t in texts]


class SlowRuntime:
    """Blocks until released, for API-level concurrency testing."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim
        self.entered = threading.Event()
        self.release = threading.Event()

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.entered.set()
        self.release.wait()
        return [[1.0] * self._dim for _ in texts]


def _build_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "model_artifact_path": "/tmp/test-model",
        "max_texts_per_request": 32,
        "max_text_length_chars": 4096,
        "max_payload_bytes": 262144,
        "max_concurrency": 1,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def settings_factory() -> Callable[..., Settings]:
    return _build_settings


@pytest.fixture()
def ready_model_state_factory() -> Callable[[str], ModelState]:
    def _make(version: str = "/tmp/test-model") -> ModelState:
        state = ModelState()
        state.mark_ready(version)
        return state

    return _make


@pytest.fixture()
def stub_runtime() -> StubRuntime:
    return StubRuntime()


@pytest.fixture()
def slow_runtime() -> SlowRuntime:
    return SlowRuntime()


@pytest.fixture()
def inference_service_factory(
    settings_factory: Callable[..., Settings],
    ready_model_state_factory: Callable[[str], ModelState],
) -> Callable[..., InferenceService]:
    def _make(
        *,
        settings: Settings | None = None,
        model_state: ModelState | None = None,
        runtime: object | None = None,
    ) -> InferenceService:
        effective_settings = settings or settings_factory()
        effective_state = model_state or ready_model_state_factory()
        effective_runtime = runtime or StubRuntime()
        return InferenceService(
            effective_settings,
            effective_state,
            effective_runtime,  # type: ignore[arg-type]
        )

    return _make


@pytest.fixture()
def app_factory() -> Callable[..., FastAPI]:
    def _make(
        *,
        settings: Settings,
        model_state: ModelState,
        inference_service: InferenceService | None = None,
    ) -> FastAPI:
        return create_app(
            settings=settings,
            model_state=model_state,
            inference_service=inference_service,
        )

    return _make


@pytest.fixture()
def ready_app(
    app_factory: Callable[..., FastAPI],
    inference_service_factory: Callable[..., InferenceService],
    settings_factory: Callable[..., Settings],
    ready_model_state_factory: Callable[[str], ModelState],
    stub_runtime: StubRuntime,
) -> FastAPI:
    """App with a loaded (ready) model and stub inference service."""
    settings = settings_factory()
    model_state = ready_model_state_factory()
    inference_service = inference_service_factory(
        settings=settings,
        model_state=model_state,
        runtime=stub_runtime,
    )
    return app_factory(
        settings=settings,
        model_state=model_state,
        inference_service=inference_service,
    )


@pytest.fixture()
def not_ready_app(
    app_factory: Callable[..., FastAPI],
    settings_factory: Callable[..., Settings],
) -> FastAPI:
    """App with model not loaded — no inference service."""
    settings = settings_factory()
    model_state = ModelState()
    return app_factory(
        settings=settings,
        model_state=model_state,
    )


@pytest.fixture()
async def ready_client(ready_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=ready_app),
        base_url="https://test",
    ) as client:
        yield client


@pytest.fixture()
async def not_ready_client(
    not_ready_app: FastAPI,
) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=not_ready_app),
        base_url="https://test",
    ) as client:
        yield client
