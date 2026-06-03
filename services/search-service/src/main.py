from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api.v1.router import api_v1_router
from src.api.v1.routers.internal import router as internal_router
from src.bootstrap import build_production_container
from src.core.config import Settings, get_settings
from src.core.dependencies import DependencyContainer
from src.middlewares.error_handler import register_exception_handlers
from src.middlewares.trace import TraceIdMiddleware


def create_app(
    settings: Settings | None = None,
    container: DependencyContainer | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    Production path: called with no container, builds a fully wired one.
    Test path: caller supplies a pre-built container (e.g. with mocks).
    """
    app_settings = settings or get_settings()

    resolved_container = container or build_production_container(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = resolved_container
        container_astart = getattr(resolved_container, "astart", None)
        if callable(container_astart):
            await container_astart()
        try:
            yield
        finally:
            await resolved_container.aclose()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)

    @app.get("/health", tags=["system"])
    async def healthcheck() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(internal_router)
    app.include_router(api_v1_router, prefix=app_settings.api_v1_prefix)

    return app
