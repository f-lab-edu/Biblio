"""Admin router skeleton.

Fixes endpoint ownership and DI wiring. Full admin-control behavior (training
trigger, rollback orchestration, role-based auth, dashboard queries) lands in
the Admin Control Plane branch.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.core.config import Settings
from src.core.dependencies import (
    get_broker_client,
    get_db_session_factory,
    get_settings_dependency,
)
from src.infra.broker import BrokerClient
from src.middlewares.auth import AuthenticatedUser, require_admin_user
from src.middlewares.trace import coerce_uuid
from src.schemas.video_dto import ErrorResponse
from src.services.admin_service import AdminService

AdminUser = Annotated[AuthenticatedUser, Depends(require_admin_user)]
DbSessionFactoryDependency = Annotated[Any, Depends(get_db_session_factory)]
BrokerClientDependency = Annotated[BrokerClient, Depends(get_broker_client)]
SettingsDependency = Annotated[Settings, Depends(get_settings_dependency)]


def _request_trace_id(request: Request) -> UUID:
    return coerce_uuid(getattr(request.state, "trace_id", None))


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
    user: AdminUser,
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
    status_code=status.HTTP_202_ACCEPTED,
)
async def rollback_model_release(
    request: Request,
    user: AdminUser,
    db_session_factory: DbSessionFactoryDependency,
    broker_client: BrokerClientDependency,
    settings: SettingsDependency,
) -> dict[str, bool]:
    """Validate the current release and publish a rollback request.

    Core API does not execute the rollback. It publishes a `ROLLBACK_REQUEST`
    control message to the rollback queue; the feedback-loop-pipeline rollback
    worker performs the actual rollback.
    """
    _ = user
    service = AdminService(
        db_session_factory=db_session_factory,
        broker_client=broker_client,
    )
    return await service.trigger_rollback(
        trace_id=_request_trace_id(request),
        rollback_queue_name=settings.feedback_rollback_queue_name,
    )
