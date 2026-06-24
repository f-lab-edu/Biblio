"""Skeleton feedback repository.

Only provides the read projection used by the feedback-router skeleton to
locate the `SearchResponseSnapshot` that a feedback event refers to. Publish
and raw-log sink responsibilities belong to the Feedback Ingestion Pipeline
branch.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.admin_ops import SearchResponseSnapshot


@dataclass(slots=True)
class SnapshotProjection:
    req_id: UUID
    user_id: UUID
    project_id: UUID
    query_text: str
    topk_chunk_ids: list[str]
    used_chunk_ids: list[str]
    active_model_version: str
    active_index_name: str
    served_vector_paths: list[dict[str, str]]
    expires_at: datetime


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_snapshot(self, req_id: UUID) -> SnapshotProjection | None:
        result = await self.session.execute(
            select(
                SearchResponseSnapshot.req_id,
                SearchResponseSnapshot.user_id,
                SearchResponseSnapshot.project_id,
                SearchResponseSnapshot.query_text,
                SearchResponseSnapshot.topk_chunk_ids,
                SearchResponseSnapshot.used_chunk_ids,
                SearchResponseSnapshot.active_model_version,
                SearchResponseSnapshot.active_index_name,
                SearchResponseSnapshot.served_vector_paths,
                SearchResponseSnapshot.expires_at,
            ).where(SearchResponseSnapshot.req_id == req_id)
        )
        row = result.one_or_none()
        if row is None:
            return None
        return SnapshotProjection(
            req_id=row.req_id,
            user_id=row.user_id,
            project_id=row.project_id,
            query_text=row.query_text,
            topk_chunk_ids=row.topk_chunk_ids,
            used_chunk_ids=row.used_chunk_ids,
            active_model_version=row.active_model_version,
            active_index_name=row.active_index_name,
            served_vector_paths=row.served_vector_paths,
            expires_at=row.expires_at,
        )
