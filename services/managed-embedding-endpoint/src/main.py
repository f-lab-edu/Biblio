from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.v1.router import router as api_v1_router
from src.core.admission_control import AdmissionController
from src.core.bootstrap import bootstrap
from src.core.model_state import ModelState
from src.core.settings import Settings, get_settings
from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.trace import TraceIdMiddleware
from src.observability.logging import error
from src.services.inference_service import InferenceService
from src.services.model_reloader import ModelRuntimeReloader


def create_app(
    settings: Settings | None = None,
    model_state: ModelState | None = None,
    inference_service: InferenceService | None = None,
    model_reloader: ModelRuntimeReloader | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Production path: called with no arguments — bootstrap wires real dependencies.
    Test path: caller supplies pre-built model_state / inference_service.
    """
    app_settings = settings or get_settings()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.settings = app_settings
    app.state.admission_controller = AdmissionController(
        app_settings.max_concurrency,
        search_request_limit=app_settings.search_request_limit,
        video_preprocess_request_limit=app_settings.video_preprocess_request_limit,
        search_wait_timeout_sec=app_settings.search_wait_timeout_sec,
        video_preprocess_wait_timeout_sec=(
            app_settings.video_preprocess_wait_timeout_sec
        ),
    )

    if model_state is not None:
        # Test path: use injected dependencies.
        app.state.model_state = model_state
        if inference_service is not None:
            app.state.inference_service = inference_service
        if model_reloader is not None:
            app.state.model_reloader = model_reloader
    else:
        # Production path: bootstrap real dependencies.
        boot_model_state, boot_service, boot_reloader = bootstrap(app_settings)
        app.state.model_state = boot_model_state
        app.state.model_reloader = boot_reloader
        app.state.inference_service = boot_service

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(api_v1_router)

    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await reload_models_on_startup(app)
    yield


async def reload_models_on_startup(app: FastAPI) -> None:
    reloader: ModelRuntimeReloader | None = getattr(
        app.state,
        "model_reloader",
        None,
    )
    if reloader is None:
        return
    try:
        await reloader.reload(trace_id="startup")
    except Exception as exc:
        error("model.startup_reload_failed", error=str(exc), trace_id="startup")
