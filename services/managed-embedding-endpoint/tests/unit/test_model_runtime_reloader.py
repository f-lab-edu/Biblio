import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep

import pytest

from src.core.model_state import ModelState
from src.core.runtime_registry import RuntimeRegistry
from src.core.settings import Settings
from src.infra.release_repository import ModelReleaseSnapshot
from src.infra.runtime import EmbeddingRuntime
from src.middlewares.error_handler import ServiceUnavailableError
from src.services.model_reloader import ModelRuntimeReloader


class _StubRuntime:
    def __init__(self, value: float) -> None:
        self._value = value
        self.closed = False

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[self._value] for _ in texts]

    def close(self) -> None:
        self.closed = True


class _FakeReleaseRepository:
    def __init__(self, snapshot: ModelReleaseSnapshot | None) -> None:
        self._snapshot = snapshot

    async def get_current(self) -> ModelReleaseSnapshot | None:
        return self._snapshot


class _FakeLoader:
    def __init__(
        self,
        state: ModelState,
        values_by_path: dict[str, float],
        failing_paths: set[str],
    ) -> None:
        self._state = state
        self._values_by_path = values_by_path
        self._failing_paths = failing_paths

    def load(self, artifact_path: str) -> EmbeddingRuntime:
        if artifact_path in self._failing_paths:
            raise FileNotFoundError(artifact_path)
        version = Path(artifact_path).name
        self._state.mark_ready(version)
        return _StubRuntime(self._values_by_path[artifact_path])


class _FakeMaterializer:
    def __init__(self, paths_by_version: dict[str, str]) -> None:
        self.calls: list[str] = []
        self._paths_by_version = paths_by_version

    def materialize(self, model_version: str) -> str:
        self.calls.append(model_version)
        return self._paths_by_version[model_version]


class _SlowMaterializer:
    def __init__(self, artifact_path: str) -> None:
        self._artifact_path = artifact_path

    def materialize(self, model_version: str) -> str:
        sleep(0.05)
        return self._artifact_path


@dataclass(slots=True)
class _LoaderFactory:
    values_by_path: dict[str, float]
    failing_paths: set[str]

    def __call__(self, state: ModelState) -> _FakeLoader:
        return _FakeLoader(state, self.values_by_path, self.failing_paths)


def _reloader(
    *,
    tmp_path: Path,
    snapshot: ModelReleaseSnapshot | None,
    values_by_version: dict[str, float],
    failing_versions: set[str] | None = None,
) -> tuple[ModelRuntimeReloader, RuntimeRegistry, ModelState]:
    root = tmp_path / "models"
    settings = Settings(
        MODEL_ARTIFACT_PATH=str(root / "active-v1"),
        MODEL_ARTIFACT_ROOT=str(root),
    )
    values_by_path = {
        str(root / version): value for version, value in values_by_version.items()
    }
    for version in values_by_version:
        (root / version).mkdir(parents=True, exist_ok=True)
    failing_paths = {str(root / version) for version in failing_versions or set()}
    state = ModelState()
    registry = RuntimeRegistry()
    return (
        ModelRuntimeReloader(
            settings=settings,
            model_state=state,
            runtime_registry=registry,
            release_repository=_FakeReleaseRepository(snapshot),
            loader_factory=_LoaderFactory(values_by_path, failing_paths),
        ),
        registry,
        state,
    )


def _reloader_with_materializer(
    *,
    tmp_path: Path,
    snapshot: ModelReleaseSnapshot,
) -> tuple[ModelRuntimeReloader, _FakeMaterializer]:
    root = tmp_path / "models"
    materialized_path = root / "active-v2"
    settings = Settings(
        MODEL_ARTIFACT_PATH=str(root / "active-v1"),
        MODEL_ARTIFACT_ROOT=str(root),
    )
    materializer = _FakeMaterializer({"active-v2": str(materialized_path)})
    values_by_path = {str(materialized_path): 2.0}
    reloader = ModelRuntimeReloader(
        settings=settings,
        model_state=ModelState(),
        runtime_registry=RuntimeRegistry(),
        release_repository=_FakeReleaseRepository(snapshot),
        loader_factory=_LoaderFactory(values_by_path, set()),
        artifact_materializer=materializer,
    )
    return reloader, materializer


class TestModelRuntimeReloader:
    async def test_keeps_existing_runtime_available_while_loading(
        self,
        tmp_path: Path,
    ):
        root = tmp_path / "models"
        materialized_path = root / "active-v2"
        settings = Settings(
            MODEL_ARTIFACT_PATH=str(root / "active-v1"),
            MODEL_ARTIFACT_ROOT=str(root),
        )
        state = ModelState()
        state.replace_ready_versions(["active-v1"])
        registry = RuntimeRegistry()
        registry.replace({"active-v1": _StubRuntime(1.0)})
        materializer = _SlowMaterializer(str(materialized_path))
        reloader = ModelRuntimeReloader(
            settings=settings,
            model_state=state,
            runtime_registry=registry,
            release_repository=_FakeReleaseRepository(
                ModelReleaseSnapshot(active_model_version="active-v2")
            ),
            loader_factory=_LoaderFactory({str(materialized_path): 2.0}, set()),
            artifact_materializer=materializer,
        )

        reload_task = asyncio.create_task(reloader.reload(trace_id="trace-1"))
        await asyncio.sleep(0.01)
        assert registry.get("active-v1").encode(["x"]) == [[1.0]]
        assert state.ready_model_versions == ["active-v1"]

        result = await reload_task

        assert result.ready_model_versions == ["active-v2"]
        assert registry.get("active-v2").encode(["x"]) == [[2.0]]
        assert registry.get("active-v1") is None

    async def test_materializes_model_version_before_loading(self, tmp_path: Path):
        snapshot = ModelReleaseSnapshot(active_model_version="active-v2")
        reloader, materializer = _reloader_with_materializer(
            tmp_path=tmp_path,
            snapshot=snapshot,
        )

        result = await reloader.reload(trace_id="trace-1")

        assert result.ready_model_versions == ["active-v2"]
        assert materializer.calls == ["active-v2"]

    async def test_loads_active_previous_candidate_from_model_release(
        self,
        tmp_path: Path,
    ):
        snapshot = ModelReleaseSnapshot(
            active_model_version="active-v2",
            previous_model_version="active-v1",
            candidate_model_version="candidate-v3",
        )
        reloader, registry, state = _reloader(
            tmp_path=tmp_path,
            snapshot=snapshot,
            values_by_version={
                "active-v2": 2.0,
                "active-v1": 1.0,
                "candidate-v3": 3.0,
            },
        )

        result = await reloader.reload(trace_id="trace-1")

        assert result.ready_model_versions == [
            "active-v2",
            "active-v1",
            "candidate-v3",
        ]
        assert state.ready_model_versions == result.ready_model_versions
        assert registry.get("candidate-v3").encode(["x"]) == [[3.0]]

    async def test_previous_candidate_load_failure_keeps_active_ready(
        self,
        tmp_path: Path,
    ):
        snapshot = ModelReleaseSnapshot(
            active_model_version="active-v2",
            previous_model_version="active-v1",
            candidate_model_version="candidate-v3",
        )
        reloader, registry, state = _reloader(
            tmp_path=tmp_path,
            snapshot=snapshot,
            values_by_version={"active-v2": 2.0},
            failing_versions={"active-v1", "candidate-v3"},
        )

        result = await reloader.reload(trace_id="trace-1")

        assert result.ready_model_versions == ["active-v2"]
        assert state.ready_model_versions == ["active-v2"]
        assert registry.get("active-v2").encode(["x"]) == [[2.0]]
        assert registry.get("active-v1") is None

    async def test_active_load_failure_preserves_existing_registry(
        self,
        tmp_path: Path,
    ):
        snapshot = ModelReleaseSnapshot(active_model_version="active-v2")
        reloader, registry, state = _reloader(
            tmp_path=tmp_path,
            snapshot=snapshot,
            values_by_version={"active-v1": 1.0},
            failing_versions={"active-v2"},
        )
        registry.replace({"active-v1": _StubRuntime(1.0)})
        state.replace_ready_versions(["active-v1"])

        with pytest.raises(ServiceUnavailableError, match="active model load failed"):
            await reloader.reload(trace_id="trace-1")

        assert state.ready_model_versions == ["active-v1"]
        assert registry.get("active-v1").encode(["x"]) == [[1.0]]

    async def test_unloads_stale_runtime_after_successful_swap(self, tmp_path: Path):
        snapshot = ModelReleaseSnapshot(active_model_version="active-v2")
        reloader, registry, state = _reloader(
            tmp_path=tmp_path,
            snapshot=snapshot,
            values_by_version={"active-v2": 2.0},
        )
        stale = _StubRuntime(1.0)
        registry.replace({"active-v1": stale})
        state.replace_ready_versions(["active-v1"])

        await reloader.reload(trace_id="trace-1")

        assert stale.closed is True
        assert registry.get("active-v1") is None
