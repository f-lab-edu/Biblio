from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from src.core.dependencies import (
    get_feedback_service,
)
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.schemas.feedback_dto import FeedbackRequest
from src.schemas.video_dto import ErrorResponse
from src.services.feedback_service import FeedbackService

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
FeedbackServiceDependency = Annotated[FeedbackService, Depends(get_feedback_service)]


feedbacks_router = APIRouter(
    prefix="/feedbacks",
    tags=["feedbacks"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)


@feedbacks_router.post("", status_code=status.HTTP_201_CREATED)
async def create_feedback(
    request: Request,
    payload: FeedbackRequest,
    user: CurrentUser,
    feedback_service: FeedbackServiceDependency,
) -> None:
    await feedback_service.record_request(
        payload,
        requester_user_id=user.requester_user_id,
        trace_id=UUID(str(request.state.trace_id)),
    )
