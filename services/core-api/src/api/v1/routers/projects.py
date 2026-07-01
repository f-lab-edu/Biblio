from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status

from src.core.dependencies import get_broker_client, get_db_session_factory
from src.infra.broker import BrokerClient
from src.middlewares.auth import AuthenticatedUser, get_current_user
from src.middlewares.error_handler import ensure_trace_id
from src.schemas.project_dto import (
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)
from src.schemas.video_dto import ErrorResponse
from src.services.project_service import ProjectService

CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
DbSessionFactoryDependency = Annotated[Any, Depends(get_db_session_factory)]
BrokerClientDependency = Annotated[BrokerClient, Depends(get_broker_client)]

projects_router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)


@projects_router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    user: CurrentUser,
    db_session_factory: DbSessionFactoryDependency,
) -> ProjectResponse:
    return await ProjectService(db_session_factory).create_project(
        payload,
        requester_user_id=user.requester_user_id,
    )


@projects_router.get("")
async def list_projects(
    user: CurrentUser,
    db_session_factory: DbSessionFactoryDependency,
) -> list[ProjectResponse]:
    return await ProjectService(db_session_factory).list_projects(
        requester_user_id=user.requester_user_id,
    )


@projects_router.patch("/{project_id}")
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    user: CurrentUser,
    db_session_factory: DbSessionFactoryDependency,
) -> ProjectResponse:
    return await ProjectService(db_session_factory).update_project(
        project_id,
        payload,
        requester_user_id=user.requester_user_id,
    )


@projects_router.delete("/{project_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_project(
    request: Request,
    project_id: UUID,
    user: CurrentUser,
    db_session_factory: DbSessionFactoryDependency,
    broker_client: BrokerClientDependency,
) -> None:
    await ProjectService(db_session_factory, broker_client).delete_project(
        project_id,
        requester_user_id=user.requester_user_id,
        trace_id=ensure_trace_id(request),
    )
