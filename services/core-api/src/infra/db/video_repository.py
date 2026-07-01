from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.cursor import KeysetCursor, decode_cursor, encode_cursor
from src.models.video import Video


@dataclass(slots=True)
class VideoPage:
    items: list[Video]
    next_cursor: str | None


class VideoRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, video: Video) -> Video:
        self.session.add(video)
        await self.session.flush()
        return video

    async def get_by_id(self, video_id: UUID) -> Video | None:
        result = await self.session.execute(
            select(Video).where(
                Video.id == video_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_user(self, video_id: UUID, requester_user_id: UUID) -> Video | None:
        result = await self.session.execute(
            select(Video).where(
                Video.id == video_id,
                Video.user_id == requester_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_ids_for_user(self, video_ids: list[UUID], requester_user_id: UUID) -> list[Video]:
        unique_video_ids = list(dict.fromkeys(video_ids))
        if not unique_video_ids:
            return []

        result = await self.session.execute(
            select(Video).where(
                Video.id.in_(unique_video_ids),
                Video.user_id == requester_user_id,
            )
        )
        videos_by_id = {video.id: video for video in result.scalars().all()}
        return [videos_by_id[video_id] for video_id in unique_video_ids if video_id in videos_by_id]

    async def list_for_user(
        self,
        requester_user_id: UUID,
        *,
        project_id: UUID | None = None,
        limit: int = 20,
        cursor: str | None = None,
    ) -> VideoPage:
        effective_limit = max(1, min(limit, 50))
        statement: Select[tuple[Video]] = (
            select(Video)
            .where(
                Video.user_id == requester_user_id,
                Video.status != "DELETING",
            )
            .order_by(Video.created_at.desc(), Video.id.desc())
            .limit(effective_limit + 1)
        )

        if project_id is not None:
            statement = statement.where(Video.project_id == project_id)

        if cursor is not None:
            decoded_cursor = decode_cursor(cursor)
            statement = statement.where(
                or_(
                    Video.created_at < decoded_cursor.created_at,
                    and_(
                        Video.created_at == decoded_cursor.created_at,
                        Video.id < decoded_cursor.id,
                    ),
                )
            )

        result = await self.session.execute(statement)
        rows = list(result.scalars())
        has_more = len(rows) > effective_limit
        items = rows[:effective_limit]
        next_cursor = None

        if has_more and items:
            next_cursor = encode_cursor(
                KeysetCursor(created_at=items[-1].created_at, id=items[-1].id)
            )

        return VideoPage(items=items, next_cursor=next_cursor)
