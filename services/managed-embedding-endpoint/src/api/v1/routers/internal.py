from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.middlewares.error_handler import ServiceUnavailableError
from src.services.model_reloader import ModelRuntimeReloader

router = APIRouter(prefix="/internal")


class ReloadModelsRequest(BaseModel):
    trace_id: str | None = None


class ReloadModelsResponse(BaseModel):
    ready_model_versions: list[str]


@router.post("/reload-models")
async def reload_models(
    request: Request,
    body: ReloadModelsRequest,
) -> ReloadModelsResponse:
    reloader: ModelRuntimeReloader | None = getattr(
        request.app.state,
        "model_reloader",
        None,
    )
    if reloader is None:
        raise ServiceUnavailableError("Model reloader is not configured.")
    result = await reloader.reload(trace_id=body.trace_id or request.state.trace_id)
    return ReloadModelsResponse(ready_model_versions=result.ready_model_versions)
