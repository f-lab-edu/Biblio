from collections.abc import Callable
from dataclasses import dataclass

from src.core.artifact_resolver import ModelArtifactResolver
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
    ) -> None:
        self._settings = settings
        self._model_state = model_state
        self._runtime_registry = runtime_registry
        self._release_repository = release_repository
        self._loader_factory = loader_factory
        self._artifact_resolver = ModelArtifactResolver(settings)
    '''
    1. model_release 읽음
    2. active/previous/candidate target 목록 만듦
    3. 현재 runtime 목록 가져옴
    4. target별로 기존 runtime 재사용 또는 새로 load
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
        new_runtimes: dict[str, EmbeddingRuntime] = {}
        ready_versions: list[str] = []

        for target in targets:
            runtime = self._load_or_reuse_runtime(
                target,
                current_runtimes,
                trace_id,
            )
            if runtime is None:
                continue
            if target.model_version in new_runtimes:
                continue
            new_runtimes[target.model_version] = runtime
            ready_versions.append(target.model_version)

        previous_runtimes = self._runtime_registry.replace(new_runtimes)
        self._model_state.replace_ready_versions(ready_versions)
        self._unload_stale_runtimes(previous_runtimes, new_runtimes, trace_id)

        info(
            "model.reload.success",
            ready_model_versions=ready_versions,
            trace_id=trace_id,
        )
        return ReloadResult(ready_model_versions=ready_versions)

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

        artifact_path = self._artifact_resolver.resolve(target.model_version)
        temp_state = ModelState()
        loader = self._loader_factory(temp_state)
        try:
            runtime = loader.load(artifact_path)
        except Exception as exc:
            if target.role == "active":
                error(
                    "model.reload.active_failed",
                    model_version=target.model_version,
                    artifact_path=artifact_path,
                    error=str(exc),
                    trace_id=trace_id,
                )
                raise ServiceUnavailableError("active model load failed") from exc
            warning(
                "model.reload.optional_failed",
                role=target.role,
                model_version=target.model_version,
                artifact_path=artifact_path,
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
