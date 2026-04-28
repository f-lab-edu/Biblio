"""Feedback router skeleton.

Fixes endpoint ownership and DI wiring. Full validation/publish flow lands in
the Feedback Ingestion Pipeline branch.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from src.core.dependencies import get_broker_client, get_db_session_factory
from src.infra.broker import BrokerClient
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.schemas.feedback_dto import FeedbackRequest
from src.schemas.video_dto import ErrorResponse
from src.services.feedback_service import FeedbackService

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
DbSessionFactoryDependency = Annotated[Any, Depends(get_db_session_factory)]
BrokerClientDependency = Annotated[BrokerClient, Depends(get_broker_client)]


feedbacks_router = APIRouter(
    prefix="/feedbacks",
    tags=["feedbacks"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
)


@feedbacks_router.post("", status_code=status.HTTP_501_NOT_IMPLEMENTED)
def create_feedback(
    payload: FeedbackRequest,
    user: CurrentUser,
    db_session_factory: DbSessionFactoryDependency,
    broker_client: BrokerClientDependency,
) -> None:
    """Skeleton endpoint.

    Validation/publish flow lands in the Feedback Ingestion Pipeline branch.
    The skeleton enforces auth + payload shape and then rejects with 501 so
    callers do not assume the behavior exists yet.
    """
    _ = user
    service = FeedbackService(
        db_session_factory=db_session_factory,
        broker_client=broker_client,
    )
    try:
        service.record_request(payload)
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
