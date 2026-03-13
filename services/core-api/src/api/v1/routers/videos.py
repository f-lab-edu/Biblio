from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response, status

from src.core.dependencies import get_broker_client, get_db_session_factory, get_storage_client
from src.infra.broker import BrokerClient
from src.infra.storage import StorageClient
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.middlewares.error_handler import ensure_trace_id
from src.schemas.video_dto import (
    DeleteVideoResponse,
    ErrorResponse,
    ExternalUrlVideoCreateResponse,
    LocalFileVideoCreateResponse,
    PlaybackUrlResponse,
    RetryVideoResponse,
    VideoCompleteRequest,
    VideoCompleteResponse,
    VideoCreateRequest,
    VideoListResponse,
    VideoMutationRequest,
    VideoResponse,
)
from src.services.video_service import VideoService

videos_router = APIRouter(
    prefix="/videos",
    tags=["videos"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)


@videos_router.post(
    "",
    response_model=LocalFileVideoCreateResponse | ExternalUrlVideoCreateResponse,
    status_code=status.HTTP_201_CREATED,
    responses={202: {"model": ExternalUrlVideoCreateResponse}},
)
async def create_video(
    request: Request,
    response: Response,
    payload: VideoCreateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
    storage_client: StorageClient = Depends(get_storage_client),
    broker_client: BrokerClient = Depends(get_broker_client),
) -> LocalFileVideoCreateResponse | ExternalUrlVideoCreateResponse:
    trace_id = _request_trace_id(request)
    result = await VideoService(
        db_session_factory=db_session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    ).create_video(
        payload,
        requester_user_id=user.requester_user_id,
        trace_id=trace_id,
    )
    request.state.video_id = str(result.payload.video_id)
    response.status_code = result.status_code
    return result.payload


@videos_router.post(
    "/{video_id}/complete",
    response_model=VideoCompleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": VideoCompleteResponse}},
)
async def complete_video(
    request: Request,
    response: Response,
    video_id: UUID,
    payload: VideoCompleteRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
    storage_client: StorageClient = Depends(get_storage_client),
    broker_client: BrokerClient = Depends(get_broker_client),
) -> VideoCompleteResponse:
    request.state.video_id = str(video_id)
    result = await VideoService(
        db_session_factory=db_session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    ).complete_video(
        video_id,
        payload,
        requester_user_id=user.requester_user_id,
        trace_id=_request_trace_id(request),
    )
    response.status_code = result.status_code
    return result.payload


@videos_router.get("", response_model=VideoListResponse)
async def list_videos(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
) -> VideoListResponse:
    return await VideoService(
        db_session_factory=db_session_factory,
        storage_client=None,
        broker_client=None,
    ).list_videos(
        requester_user_id=user.requester_user_id,
        limit=limit,
        cursor=cursor,
    )


@videos_router.get("/{video_id}", response_model=VideoResponse)
async def get_video(
    request: Request,
    video_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
) -> VideoResponse:
    request.state.video_id = str(video_id)
    return await VideoService(
        db_session_factory=db_session_factory,
        storage_client=None,
        broker_client=None,
    ).get_video(
        video_id,
        requester_user_id=user.requester_user_id,
    )


@videos_router.patch("/{video_id}", response_model=VideoResponse)
async def update_video(
    request: Request,
    video_id: UUID,
    payload: VideoMutationRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
) -> VideoResponse:
    request.state.video_id = str(video_id)
    return await VideoService(
        db_session_factory=db_session_factory,
        storage_client=None,
        broker_client=None,
    ).update_video(
        video_id,
        payload,
        requester_user_id=user.requester_user_id,
    )


@videos_router.delete("/{video_id}", response_model=DeleteVideoResponse, status_code=status.HTTP_202_ACCEPTED)
async def delete_video(
    request: Request,
    response: Response,
    video_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
    broker_client: BrokerClient = Depends(get_broker_client),
) -> DeleteVideoResponse:
    request.state.video_id = str(video_id)
    result = await VideoService(
        db_session_factory=db_session_factory,
        storage_client=None,
        broker_client=broker_client,
    ).delete_video(
        video_id,
        requester_user_id=user.requester_user_id,
        trace_id=_request_trace_id(request),
    )
    response.status_code = result.status_code
    return result.payload


@videos_router.post(
    "/{video_id}/playback-url",
    response_model=PlaybackUrlResponse,
)
async def issue_playback_url(
    request: Request,
    video_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
    storage_client: StorageClient = Depends(get_storage_client),
) -> PlaybackUrlResponse:
    request.state.video_id = str(video_id)
    return await VideoService(
        db_session_factory=db_session_factory,
        storage_client=storage_client,
        broker_client=None,
    ).issue_playback_url(
        video_id,
        requester_user_id=user.requester_user_id,
    )


@videos_router.post(
    "/{video_id}/retry",
    response_model=RetryVideoResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_video(
    request: Request,
    response: Response,
    video_id: UUID,
    user: AuthenticatedUser = Depends(get_current_user),
    db_session_factory=Depends(get_db_session_factory),
    broker_client: BrokerClient = Depends(get_broker_client),
) -> RetryVideoResponse:
    request.state.video_id = str(video_id)
    result = await VideoService(
        db_session_factory=db_session_factory,
        storage_client=None,
        broker_client=broker_client,
    ).retry_video(
        video_id,
        requester_user_id=user.requester_user_id,
        trace_id=_request_trace_id(request),
    )
    response.status_code = result.status_code
    return result.payload


def _request_trace_id(request: Request) -> UUID:
    try:
        return UUID(ensure_trace_id(request))
    except ValueError:
        trace_id = uuid4()
        request.state.trace_id = str(trace_id)
        return trace_id
