"""Skeleton admin repository for projection reads.

Full business logic is implemented in the admin-control branch. This file only
pins the query entry points so router/service skeletons can wire to a stable
shape.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.admin_ops import MLPipelineRun, Project
from src.models.video import Video


@dataclass(slots=True)
class ProjectProjection:
    id: UUID
    user_id: UUID
    search_serving_state: str


@dataclass(slots=True)
class MLPipelineRunProjection:
    id: UUID
    status: str
    candidate_model_version: str | None
    dataset_version: str


class AdminRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_project(self, project_id: UUID) -> ProjectProjection | None:
        result = await self.session.execute(
            select(Project.id, Project.user_id, Project.search_serving_state).where(
                Project.id == project_id
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        return ProjectProjection(
            id=row.id,
            user_id=row.user_id,
            search_serving_state=row.search_serving_state,
        )

    async def get_video(self, video_id: UUID) -> Video | None:
        result = await self.session.execute(
            select(Video).where(Video.id == video_id)
        )
        return result.scalar_one_or_none()

    async def get_ml_pipeline_run(self, run_id: UUID) -> MLPipelineRunProjection | None:
        result = await self.session.execute(
            select(
                MLPipelineRun.id,
                MLPipelineRun.status,
                MLPipelineRun.candidate_model_version,
                MLPipelineRun.dataset_version,
            ).where(MLPipelineRun.id == run_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return MLPipelineRunProjection(
            id=row.id,
            status=row.status,
            candidate_model_version=row.candidate_model_version,
            dataset_version=row.dataset_version,
        )
