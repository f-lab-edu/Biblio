from src.core.model_state import ModelState
from src.core.settings import Settings
from src.infra.loader_factory import build_model_loader
from src.infra.model_loader import ModelLoader
from src.infra.runtime import EmbeddingRuntime
from src.observability.logging import error, info
from src.services.inference_service import InferenceService


def bootstrap(settings: Settings) -> tuple[ModelState, InferenceService | None]:
    """Wire production dependencies at startup.

    Returns (model_state, inference_service_or_none).
    On model-load failure the process stays alive; /health and /embed will return 503.
    """
    model_state = ModelState()
    loader = build_model_loader(settings, model_state)

    runtime = _try_load_model(loader, settings.model_artifact_path)

    if runtime is None:
        return model_state, None

    info(
        "model.load.success",
        model_version=model_state.model_version,
        result="success",
    )

    inference_service = InferenceService(settings, model_state, runtime)
    return model_state, inference_service


def _try_load_model(loader: ModelLoader, artifact_path: str) -> EmbeddingRuntime | None:
    """Attempt to load the model, returning the runtime or None on failure."""
    try:
        return loader.load(artifact_path)
    except Exception as exc:
        error(
            "model.load.failed",
            model_artifact_path=artifact_path,
            error=str(exc),
            result="failure",
        )
        return None
