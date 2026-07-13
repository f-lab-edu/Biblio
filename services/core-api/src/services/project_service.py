from typing import Any
from uuid import UUID, uuid4

from src.infra.broker import BrokerClient, BrokerPublishError, build_message
from src.infra.db.project_repository import ProjectRepository, ProjectWithVideoCount
from src.middlewares.error_handler import ApiError, ConflictError, NotFoundError
from src.models.admin_ops import Project
from src.schemas.project_dto import (
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
)


class ProjectService:
    def __init__(self, db_session_factory: Any, broker_client: BrokerClient | None = None) -> None:
        self._db_session_factory = db_session_factory
        self._broker_client = broker_client

    async def create_project(
        self,
        payload: ProjectCreateRequest,
        *,
        requester_user_id: UUID,
    ) -> ProjectResponse:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            repository = ProjectRepository(session)
            project = await repository.add(
                Project(
                    id=uuid4(),
                    user_id=requester_user_id,
                    title=payload.title,
                )
            )
            await session.commit()
            return self._to_response(ProjectWithVideoCount(project=project, video_count=0))

    async def list_projects(self, *, requester_user_id: UUID) -> list[ProjectResponse]:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            repository = ProjectRepository(session)
            projects = await repository.list_for_user(requester_user_id)

        return [self._to_response(project) for project in projects]

    async def update_project(
        self,
        project_id: UUID,
        payload: ProjectUpdateRequest,
        *,
        requester_user_id: UUID,
    ) -> ProjectResponse:
        self._ensure_db_session_factory()

        async with self._db_session_factory() as session:
            repository = ProjectRepository(session)
            project = await repository.get_for_user(project_id, requester_user_id)
            if project is None:
                raise NotFoundError("Project not found.")
            if project.lifecycle_state != "ACTIVE":
                raise ConflictError("Project is being deleted.")

            await repository.update_title(project, payload.title)
            video_count = await repository.count_active_videos(project_id)
            await session.commit()
            return self._to_response(
                ProjectWithVideoCount(project=project, video_count=video_count)
            )

    async def delete_project(
        self,
        project_id: UUID,
        *,
        requester_user_id: UUID,
        trace_id: UUID,
    ) -> None:
        self._ensure_db_session_factory()
        self._ensure_broker_client()

        async with self._db_session_factory() as session:
            repository = ProjectRepository(session)
            project = await repository.get_for_user(project_id, requester_user_id)
            if project is None:
                raise NotFoundError("Project not found.")

            previous_lifecycle_state = project.lifecycle_state
            await repository.mark_deleting(project)
            await session.commit()

        try:
            await self._publish_project_delete(project_id, trace_id=trace_id)
        except ApiError:
            await self._restore_project_lifecycle_state(
                project_id,
                requester_user_id=requester_user_id,
                lifecycle_state=previous_lifecycle_state,
            )
            raise

    def _ensure_db_session_factory(self) -> None:
        if self._db_session_factory is None:
            raise ApiError("Database session factory is not configured.")

    def _ensure_broker_client(self) -> None:
        if self._broker_client is None:
            raise ApiError("Broker client is not configured.")

    async def _publish_project_delete(self, project_id: UUID, *, trace_id: UUID) -> None:
        message = build_message(
            "PROJECT_DELETE_REQUEST",
            project_id=project_id,
            trace_id=trace_id,
        )
        try:
            await self._broker_client.publish_with_retry(message)
        except BrokerPublishError as exc:
            raise ApiError("Message broker publish failed after retries.") from exc
    
    # 삭제에는 실패했는데 프로젝트만 삭제 중으로 표시되는 불일치를 막는 롤백 함수
    async def _restore_project_lifecycle_state(
        self,
        project_id: UUID,
        *,
        requester_user_id: UUID,
        lifecycle_state: str,
    ) -> None:
        async with self._db_session_factory() as session:
            repository = ProjectRepository(session)
            project = await repository.get_for_user(project_id, requester_user_id)
            if project is not None:
                project.lifecycle_state = lifecycle_state
            await session.commit()

    @staticmethod
    def _to_response(project: ProjectWithVideoCount) -> ProjectResponse:
        return ProjectResponse(
            id=project.project.id,
            title=project.project.title,
            video_count=project.video_count,
            created_at=project.project.created_at,
            updated_at=project.project.updated_at,
        )
