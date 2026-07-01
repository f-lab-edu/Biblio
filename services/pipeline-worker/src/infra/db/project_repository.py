from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import (
    LegacyReindexItemModel,
    ProjectModel,
    SearchResponseSnapshotModel,
    VectorIndexEntryModel,
)


class ProjectRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def delete_project_records(self, project_id: UUID | str) -> None:
        normalized_project_id = self._normalize_uuid(project_id)
        async with self._session_factory() as session:
            await session.execute(
                delete(SearchResponseSnapshotModel).where(
                    SearchResponseSnapshotModel.project_id == normalized_project_id
                )
            )
            await session.execute(
                delete(VectorIndexEntryModel).where(
                    VectorIndexEntryModel.project_id == normalized_project_id
                )
            )
            await session.execute(
                delete(LegacyReindexItemModel).where(
                    LegacyReindexItemModel.project_id == normalized_project_id
                )
            )
            await session.execute(
                delete(ProjectModel).where(ProjectModel.id == normalized_project_id)
            )
            await session.commit()

    @staticmethod
    def _normalize_uuid(value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value
        return UUID(str(value))
