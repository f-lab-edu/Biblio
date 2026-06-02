# 모델 변경시 DB의 ModelRelease를 다시 읽어서 search-service 메모리에 있는 검색 target을 교체

from fastapi import APIRouter, Request
from pydantic import BaseModel

from src.infra.db.search_repository import ServingSearchTargets
from src.middlewares.error_handler import ServiceUnavailableError
from src.services.serving_targets import ServingSearchTargetProvider

router = APIRouter(prefix="/internal", tags=["internal"])


class ReloadServingTargetsRequest(BaseModel):
    trace_id: str | None = None


class ServingTargetResponse(BaseModel):
    model_version: str
    index_name: str


class ReloadServingTargetsResponse(BaseModel):
    active: ServingTargetResponse
    previous: ServingTargetResponse | None = None # 필수 아님


@router.post("/reload-serving-targets")
async def reload_serving_targets(
    request: Request,
    body: ReloadServingTargetsRequest,
) -> ReloadServingTargetsResponse:
    _ = body.trace_id
    provider = _get_serving_target_provider(request)
    targets = await provider.reload()
    return _to_response(targets)


def _get_serving_target_provider(request: Request) -> ServingSearchTargetProvider:
    container = getattr(request.app.state, "container", None)
    provider = getattr(container, "serving_target_provider", None)
    if provider is None:
        raise ServiceUnavailableError("Serving target provider is not configured.")
    return provider


def _to_response(targets: ServingSearchTargets) -> ReloadServingTargetsResponse:
    previous = None
    if targets.previous is not None:
        previous = ServingTargetResponse(
            model_version=targets.previous.model_version,
            index_name=targets.previous.index_name,
        )
    return ReloadServingTargetsResponse(
        active=ServingTargetResponse(
            model_version=targets.active.model_version,
            index_name=targets.active.index_name,
        ),
        previous=previous,
    )
