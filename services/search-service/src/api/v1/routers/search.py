from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request

from src.core.dependencies import get_search_orchestrator, get_search_repository
from src.infra.db.search_repository import SearchRepository
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.middlewares.error_handler import InvalidArgumentError
from src.schemas.search_dto import (
    QUERY_MAX_LEN,
    QUERY_MIN_LEN,
    SearchHistoryChunkResponse,
    SearchHistoryResponse,
    SearchRequest,
    SearchResponse,
    normalize_query,
)
from src.services.search_orchestrator import SearchOrchestrator

router = APIRouter(tags=["search"])


@router.get("/search/history")
async def search_history(
    project_id: UUID,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    repo: Annotated[SearchRepository, Depends(get_search_repository)],
) -> list[SearchHistoryResponse]:
    conversations = await repo.list_conversations_for_project(
        user.requester_user_id,
        project_id,
    )
    return [
        SearchHistoryResponse(
            query=conversation.query,
            req_id=conversation.req_id,
            answer=conversation.answer,
            chunks=[
                SearchHistoryChunkResponse.model_validate(source)
                for source in conversation.sources
            ],
        )
        for conversation in conversations
    ]


@router.post("/search")
async def search(
    request: Request,
    body: SearchRequest,
    user: Annotated[AuthenticatedUser, Depends(get_current_user)],
    orchestrator: Annotated[
        SearchOrchestrator, Depends(get_search_orchestrator)
    ],
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
    # request.state.trace_id로 trace_id를 가져오거나, 없으면 새로 생성하여 사용
    trace_id = getattr(request.state, "trace_id", str(uuid4()))

    result = await orchestrator.execute(
        user_id=user.requester_user_id,
        project_id=body.project_id,
        query=normalized,
        trace_id=trace_id,
    )

    return SearchResponse(
        req_id=result.req_id,
        answer=result.answer,
        chunks=result.chunks,
    )
