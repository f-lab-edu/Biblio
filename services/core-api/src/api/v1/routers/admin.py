"""Admin router skeleton.

Fixes endpoint ownership and DI wiring. Full admin-control behavior (training
trigger, rollback orchestration, role-based auth, dashboard queries) lands in
the Admin Control Plane branch.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from src.core.dependencies import get_broker_client, get_db_session_factory
from src.infra.broker import BrokerClient
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.schemas.video_dto import ErrorResponse
from src.services.admin_service import AdminService

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
DbSessionFactoryDependency = Annotated[Any, Depends(get_db_session_factory)]
BrokerClientDependency = Annotated[BrokerClient, Depends(get_broker_client)]


admin_router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
        501: {"model": ErrorResponse},
    },
)


@admin_router.post(
    "/ml-pipeline-runs/retrigger",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
def retrigger_ml_pipeline(
    user: CurrentUser,
    db_session_factory: DbSessionFactoryDependency,
    broker_client: BrokerClientDependency,
) -> None:
    """Skeleton endpoint for triggering a training run.

    The admin-control branch owns the full business rules (role check,
    precondition gate, control-message publish). Foundation only reserves
    the path.
    """
    _ = user
    service = AdminService(
        db_session_factory=db_session_factory,
        broker_client=broker_client,
    )
    try:
        service.trigger_training()
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc


@admin_router.post(
    "/model-release/rollback",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
)
def rollback_model_release(
    user: CurrentUser,
    db_session_factory: DbSessionFactoryDependency,
    broker_client: BrokerClientDependency,
) -> None:
    """Skeleton endpoint for triggering a model-release rollback.

    The admin-control branch owns the full rollback orchestration.
    """
    _ = user
    service = AdminService(
        db_session_factory=db_session_factory,
        broker_client=broker_client,
    )
    try:
        service.trigger_rollback()
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(exc),
        ) from exc
