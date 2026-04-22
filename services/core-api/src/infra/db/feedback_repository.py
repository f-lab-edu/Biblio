"""Skeleton feedback repository.

Only provides the read projection used by the feedback-router skeleton to
locate the `SearchResponseSnapshot` that a feedback event refers to. Publish
and raw-log sink responsibilities belong to the Feedback Ingestion Pipeline
branch.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.admin_ops import SearchResponseSnapshot


@dataclass(slots=True)
class SnapshotProjection:
    req_id: UUID
    user_id: UUID
    project_id: UUID
    active_model_version: str
    active_index_name: str


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_snapshot(self, req_id: UUID) -> SnapshotProjection | None:
        result = await self.session.execute(
            select(
                SearchResponseSnapshot.req_id,
                SearchResponseSnapshot.user_id,
                SearchResponseSnapshot.project_id,
                SearchResponseSnapshot.active_model_version,
                SearchResponseSnapshot.active_index_name,
            ).where(SearchResponseSnapshot.req_id == req_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return SnapshotProjection(
            req_id=row.req_id,
            user_id=row.user_id,
            project_id=row.project_id,
            active_model_version=row.active_model_version,
            active_index_name=row.active_index_name,
        )
