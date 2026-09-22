'''
DB의 model_release에 적힌 버전대로, 이 VM 자신의 메모리 상태를 직접 맞추는 역할.

- 이미 메모리에 있으면 다시 안 받고 그대로 재사용 (134~136행)
- 없으면 GCS에서 받아서 새로 로드 (141~142행)
- 더 이상 필요 없어진 옛 버전은 로드 다 끝난 뒤에 메모리에서 내림 (176~196행)
'''
import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from src.core.artifact_resolver import (
    ModelArtifactMaterializer,
    build_model_artifact_materializer,
)
from src.core.model_state import ModelState
from src.core.runtime_registry import RuntimeRegistry
from src.core.settings import Settings
from src.infra.model_loader import ModelLoader
from src.infra.release_repository import ModelReleaseRepository, ModelReleaseSnapshot
from src.infra.runtime import EmbeddingRuntime
from src.middlewares.error_handler import ServiceUnavailableError
from src.observability.logging import error, info, warning


@dataclass(frozen=True, slots=True)
class ReloadResult:
    ready_model_versions: list[str]


@dataclass(frozen=True, slots=True)
class _ReloadTarget:
    role: str
    model_version: str


class ModelRuntimeReloader:
    """ModelRelease 서빙 상태에 맞춰 model runtime 목록을 다시 로드한다."""

    def __init__(
        self,
        *,
        settings: Settings,
        model_state: ModelState,
        runtime_registry: RuntimeRegistry,
        release_repository: ModelReleaseRepository,
        loader_factory: Callable[[ModelState], ModelLoader],
        artifact_materializer: ModelArtifactMaterializer | None = None,
    ) -> None:
        self._settings = settings
        self._model_state = model_state
        self._runtime_registry = runtime_registry
        self._release_repository = release_repository
        self._loader_factory = loader_factory
        self._artifact_materializer = artifact_materializer or (
            build_model_artifact_materializer(settings)
        )
    '''
    1. model_release 읽음
    2. active/previous/candidate target 목록 만듦
    3. 현재 runtime 목록 가져옴
    4. 별도 작업 스레드에서 target별로 materialize 후 기존 runtime 재사용 또는 새로 load
    5. RuntimeRegistry를 새 runtime 목록으로 교체
    6. ModelState ready_model_versions 갱신
    7. 더 이상 안 쓰는 runtime close
    8. ready_model_versions 반환
    
    '''
    async def reload(self, trace_id: str | None = None) -> ReloadResult:
        try:
            release = await self._release_repository.get_current()
        except Exception as exc:
            error("model_release.read_failed", error=str(exc), trace_id=trace_id)
            raise ServiceUnavailableError("ModelRelease read failed.") from exc

        targets = self._targets_from_release(release)
        current_runtimes = self._runtime_registry.snapshot()
        new_runtimes, ready_versions = await asyncio.to_thread(
            self._load_target_runtimes,
            targets,
            current_runtimes,
            trace_id,
        )

        previous_runtimes = self._runtime_registry.replace(new_runtimes)
        self._model_state.replace_ready_versions(ready_versions)
        self._unload_stale_runtimes(previous_runtimes, new_runtimes, trace_id)

        info(
            "model.reload.success",
            ready_model_versions=ready_versions,
            trace_id=trace_id,
        )
        return ReloadResult(ready_model_versions=ready_versions)

    def _load_target_runtimes(
        self,
        targets: list[_ReloadTarget],
        current_runtimes: dict[str, EmbeddingRuntime],
        trace_id: str | None,
    ) -> tuple[dict[str, EmbeddingRuntime], list[str]]:
        new_runtimes: dict[str, EmbeddingRuntime] = {}
        ready_versions: list[str] = []
        for target in targets:
            runtime = self._load_or_reuse_runtime(
                target,
                current_runtimes,
                trace_id,
            )
            if runtime is None or target.model_version in new_runtimes:
                continue
            new_runtimes[target.model_version] = runtime
            ready_versions.append(target.model_version)
        return new_runtimes, ready_versions

    def _targets_from_release(
        self,
        release: ModelReleaseSnapshot | None,
    ) -> list[_ReloadTarget]:
        if release is None:
            return [
                _ReloadTarget(
                    role="active",
                    model_version=self._settings.bootstrap_model_version,
                )
            ]

        targets = [_ReloadTarget("active", release.active_model_version)]
        if release.previous_model_version:
            targets.append(_ReloadTarget("previous", release.previous_model_version))
        if release.candidate_model_version:
            targets.append(_ReloadTarget("candidate", release.candidate_model_version))
        return targets

    def _load_or_reuse_runtime(
        self,
        target: _ReloadTarget,
        current_runtimes: dict[str, EmbeddingRuntime],
        trace_id: str | None,
    ) -> EmbeddingRuntime | None:
        runtime = current_runtimes.get(target.model_version)
        if runtime is not None:
            return runtime

        temp_state = ModelState()
        loader = self._loader_factory(temp_state)
        try:
            artifact_path = self._artifact_materializer.materialize(target.model_version)
            runtime = loader.load(artifact_path)
        except Exception as exc:
            if target.role == "active":
                error(
                    "model.reload.active_failed",
                    model_version=target.model_version,
                    error=str(exc),
                    trace_id=trace_id,
                )
                raise ServiceUnavailableError("active model load failed") from exc
            warning(
                "model.reload.optional_failed",
                role=target.role,
                model_version=target.model_version,
                error=str(exc),
                trace_id=trace_id,
            )
            return None

        if not temp_state.is_ready(target.model_version):
            if target.role == "active":
                raise ServiceUnavailableError(
                    f"Loaded model version mismatch: {target.model_version}."
                )
            warning(
                "model.reload.optional_version_mismatch",
                role=target.role,
                model_version=target.model_version,
                ready_model_versions=temp_state.ready_model_versions,
                trace_id=trace_id,
            )
            return None
        return runtime

    @staticmethod
    def _unload_stale_runtimes(
        previous_runtimes: dict[str, EmbeddingRuntime],
        new_runtimes: dict[str, EmbeddingRuntime],
        trace_id: str | None,
    ) -> None:
        for model_version, runtime in previous_runtimes.items():
            if model_version in new_runtimes:
                continue
            close = getattr(runtime, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:
                warning(
                    "model.reload.unload_failed",
                    model_version=model_version,
                    error=str(exc),
                    trace_id=trace_id,
                )
