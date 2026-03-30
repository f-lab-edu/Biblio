from fastapi import FastAPI

from src.api.v1.router import router as api_v1_router
from src.core.admission_control import AdmissionController
from src.core.bootstrap import bootstrap
from src.core.model_state import ModelState
from src.core.settings import Settings, get_settings
from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.trace import TraceIdMiddleware
from src.services.inference_service import InferenceService


def create_app(
    settings: Settings | None = None,
    model_state: ModelState | None = None,
    inference_service: InferenceService | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Production path: called with no arguments — bootstrap wires real dependencies.
    Test path: caller supplies pre-built model_state / inference_service.
    """
    app_settings = settings or get_settings()

    app = FastAPI(title=app_settings.app_name)
    app.state.settings = app_settings
    app.state.admission_controller = AdmissionController(app_settings.max_concurrency)

    if model_state is not None:
        # Test path: use injected dependencies.
        app.state.model_state = model_state
        if inference_service is not None:
            app.state.inference_service = inference_service
    else:
        # Production path: bootstrap real dependencies.
        boot_model_state, boot_service = bootstrap(app_settings)
        app.state.model_state = boot_model_state
        if boot_service is not None:
            app.state.inference_service = boot_service

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(api_v1_router)

    return app
