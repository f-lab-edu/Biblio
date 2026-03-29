from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from src.core.dependencies import get_search_orchestrator
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.middlewares.error_handler import InvalidArgumentError
from src.schemas.search_dto import (
    QUERY_MAX_LEN,
    QUERY_MIN_LEN,
    SearchRequest,
    SearchResponse,
    normalize_query,
)
from src.services.search_orchestrator import SearchOrchestrator

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    request: Request,
    body: SearchRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    orchestrator: SearchOrchestrator = Depends(get_search_orchestrator),
) -> SearchResponse:
    normalized = normalize_query(body.query)

    if len(normalized) < QUERY_MIN_LEN:
        raise InvalidArgumentError(
            f"Query must be at least {QUERY_MIN_LEN} characters after normalization."
        )
    if len(normalized) > QUERY_MAX_LEN:
        raise InvalidArgumentError(
            f"Query must not exceed {QUERY_MAX_LEN} characters after normalization."
        )

    trace_id = getattr(request.state, "trace_id", str(uuid4()))

    result = await orchestrator.execute(
        user_id=user.requester_user_id,
        query=normalized,
        trace_id=trace_id,
    )

    return SearchResponse(
        req_id=result.req_id,
        answer=result.answer,
        chunks=result.chunks,
    )
