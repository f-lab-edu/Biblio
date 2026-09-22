from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.infra.db.project_repository import ProjectRepository
from src.infra.db.video_repository import VideoRepository
from src.usecases.delete_video import DeleteVideoUseCase, DeleteVideoResult


@dataclass(slots=True)
class DeleteProjectResult:
    deleted_video_count: int
    duplicate_video_count: int


class DeleteProjectUseCase:
    def __init__(
        self,
        *,
        video_repository: VideoRepository,
        delete_video_use_case: DeleteVideoUseCase,
        session_factory: Any,
    ) -> None:
        self._video_repository = video_repository
        self._delete_video_use_case = delete_video_use_case
        self._project_repository = ProjectRepository(session_factory)

    async def execute(self, *, project_id: str, trace_id: str) -> DeleteProjectResult:
        await self._video_repository.mark_project_videos_deleting(project_id)
        video_ids = await self._video_repository.list_project_video_ids(project_id)
        delete_result = await self._delete_project_videos(video_ids, trace_id=trace_id)
        await self._project_repository.delete_project_records(project_id)
        return DeleteProjectResult(
            deleted_video_count=delete_result.deleted_count,
            duplicate_video_count=delete_result.duplicate_count,
        )

    async def _delete_project_videos(self, video_ids: list[UUID], *, trace_id: str) -> DeleteVideoResult:
        if not video_ids:
            return DeleteVideoResult(deleted_count=0, duplicate_count=0)
        return await self._delete_video_use_case.execute(
            video_ids=[str(video_id) for video_id in video_ids],
            trace_id=trace_id,
        )
