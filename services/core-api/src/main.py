from fastapi import FastAPI

from src.api.v1.router import api_v1_router
from src.core.config import Settings, get_settings
from src.core.dependencies import build_dependency_container
from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.trace import TraceIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    app = FastAPI(title=app_settings.app_name)
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app) # error handler 등록
    app.state.container = build_dependency_container(app_settings)
    app.include_router(api_v1_router, prefix=app_settings.api_v1_prefix)

    return app
