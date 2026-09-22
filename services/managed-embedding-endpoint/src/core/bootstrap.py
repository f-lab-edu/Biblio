from collections.abc import Callable

from src.core.artifact_resolver import build_model_artifact_materializer
from src.core.model_state import ModelState
from src.core.runtime_registry import RuntimeRegistry
from src.core.settings import Settings
from src.infra.loader_factory import build_model_loader
from src.infra.model_loader import ModelLoader
from src.infra.release_repository import (
    AsyncpgModelReleaseRepository,
    ModelReleaseRepository,
    NullModelReleaseRepository,
)
from src.infra.runtime import EmbeddingRuntime
from src.observability.logging import error, info
from src.services.inference_service import InferenceService
from src.services.model_reloader import ModelRuntimeReloader


def bootstrap(
    settings: Settings,
) -> tuple[ModelState, InferenceService | None, ModelRuntimeReloader]:
    """Wire production dependencies at startup.

    Returns (model_state, inference_service_or_none).
    On model-load failure the process stays alive; /health and /embed will return 503.
    """
    _apply_inference_thread_limit(settings.inference_threads)
    model_state = ModelState()
    runtime_registry = RuntimeRegistry()
    loader = build_model_loader(settings, model_state)
    artifact_materializer = build_model_artifact_materializer(settings)

    runtime = _try_load_model(
        loader,
        artifact_materializer.materialize,
        settings.bootstrap_model_version,
    )
    if runtime is not None and model_state.model_version:
        runtime_registry.replace({model_state.model_version: runtime})

    reloader = ModelRuntimeReloader(
        settings=settings,
        model_state=model_state,
        runtime_registry=runtime_registry,
        release_repository=_build_release_repository(settings),
        loader_factory=lambda state: build_model_loader(settings, state),
        artifact_materializer=artifact_materializer,
    )

    if runtime is not None:
        info(
            "model.load.success",
            model_version=model_state.model_version,
            result="success",
        )

    inference_service = InferenceService(
        settings=settings,
        runtime_registry=runtime_registry,
    )
    return model_state, inference_service, reloader


def _apply_inference_thread_limit(thread_count: int | None) -> None:
    """Pin the intra-op thread count before the model is loaded."""
    import torch

    if thread_count is not None:
        torch.set_num_threads(thread_count)
    info("inference.thread_limit", threads=torch.get_num_threads())


def _try_load_model(
    loader: ModelLoader,
    materialize_model: Callable[[str], str],
    model_version: str,
) -> EmbeddingRuntime | None:
    """Attempt to load the model, returning the runtime or None on failure."""
    try:
        artifact_path = materialize_model(model_version)
        return loader.load(artifact_path)
    except Exception as exc:
        error(
            "model.load.failed",
            model_version=model_version,
            error=str(exc),
            result="failure",
        )
        return None


def _build_release_repository(settings: Settings) -> ModelReleaseRepository:
    if not settings.database_url:
        return NullModelReleaseRepository()
    return AsyncpgModelReleaseRepository(settings.database_url)
