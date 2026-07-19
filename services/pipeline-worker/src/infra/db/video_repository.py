from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import AssetModel, ChunkModel, TranscriptSegmentModel, VectorIndexEntryModel, VideoModel

VideoStatus = Literal["PENDING", "UPLOADED", "PROCESSING", "READY", "FAILED", "DELETING"]


@dataclass(slots=True)
class VideoRecord:
    id: UUID | str
    user_id: UUID | str
    project_id: UUID | str | None = None
    title: str = ""
    category: str = "GENERAL"
    input_type: str = "LOCAL_FILE"
    source_url: str | None = None
    storage_path: str | None = None
    status: VideoStatus = "PENDING"
    failed_stage: str | None = None
    processing_claimed_at: datetime | None = None


@dataclass(slots=True)
class PipelineState:
    video: VideoRecord | None
    has_current_outputs: bool
    has_transcript: bool
    has_audio_asset: bool


class VideoRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        stale_processing_reclaim_sec: int,
    ) -> None:
        self._session_factory = session_factory
        self._stale_processing_reclaim_sec = stale_processing_reclaim_sec

    async def create_video(self, video: VideoRecord) -> None:
        video_id = self._normalize_uuid(video.id)
        user_id = self._normalize_uuid(video.user_id)
        project_id = (
            self._normalize_uuid(video.project_id)
            if video.project_id is not None
            else None
        )
        async with self._session_factory() as session:
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=user_id,
                    project_id=project_id,
                    title=video.title,
                    category=video.category,
                    input_type=video.input_type,
                    source_url=video.source_url,
                    storage_path=video.storage_path,
                    status=video.status,
                    failed_stage=video.failed_stage,
                )
            )
            await session.commit()

    async def get_video(self, video_id: UUID | str) -> VideoRecord | None:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            model = await session.get(VideoModel, normalized_video_id)
            if model is None:
                return None
            return self._to_record(model)

    async def get_videos(self, video_ids: list[UUID | str]) -> list[VideoRecord]:
        normalized_video_ids = self._normalize_uuids(video_ids)
        if not normalized_video_ids:
            return []

        async with self._session_factory() as session:
            result = await session.execute(
                select(VideoModel).where(VideoModel.id.in_(normalized_video_ids))
            )
            return [self._to_record(model) for model in result.scalars().all()]

    async def list_project_video_ids(self, project_id: UUID | str) -> list[UUID]:
        normalized_project_id = self._normalize_uuid(project_id)
        async with self._session_factory() as session:
            result = await session.execute(
                select(VideoModel.id).where(VideoModel.project_id == normalized_project_id)
            )
            return list(result.scalars().all())

    async def load_pipeline_state(
        self,
        video_id: UUID | str,
        *,
        stt_model_version: str,
        embedding_model_version: str,
    ) -> PipelineState:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            video = await session.get(VideoModel, normalized_video_id)
            if video is None:
                return PipelineState(video=None, has_current_outputs=False, has_transcript=False, has_audio_asset=False)

            has_transcript = bool(
                await session.scalar(
                    select(func.count())
                    .select_from(TranscriptSegmentModel)
                    .where(
                        and_(
                            TranscriptSegmentModel.video_id == normalized_video_id,
                            TranscriptSegmentModel.stt_model_version == stt_model_version,
                        )
                    )
                )
            )
            chunk_count = await session.scalar(
                select(func.count())
                .select_from(ChunkModel)
                .where(
                    and_(
                        ChunkModel.video_id == normalized_video_id,
                        ChunkModel.stt_model_version == stt_model_version,
                        ChunkModel.embedding_model_version == embedding_model_version,
                    )
                )
            )
            vector_count = await session.scalar(
                select(func.count())
                .select_from(VectorIndexEntryModel)
                .where(
                    and_(
                        VectorIndexEntryModel.video_id == normalized_video_id,
                        VectorIndexEntryModel.embedding_model_version == embedding_model_version,
                    )
                )
            )
            has_audio_asset = bool(
                await session.scalar(
                    select(func.count())
                    .select_from(AssetModel)
                    .where(and_(AssetModel.video_id == normalized_video_id, AssetModel.asset_type == "AUDIO"))
                )
            )
            return PipelineState(
                video=self._to_record(video),
                has_current_outputs=bool(chunk_count and vector_count and chunk_count == vector_count),
                has_transcript=has_transcript,
                has_audio_asset=has_audio_asset,
            )

    async def claim_processing(
        self,
        video_id: UUID | str,
        *,
        keep_ready_status: bool = False,
    ) -> bool:
        normalized_video_id = self._normalize_uuid(video_id)
        stale_cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self._stale_processing_reclaim_sec
        )
        async with self._session_factory() as session:
            statement = update(VideoModel).where(VideoModel.id == normalized_video_id)
            if keep_ready_status:
                statement = statement.where(
                    and_(
                        VideoModel.status == "READY",
                        or_(
                            VideoModel.processing_claimed_at.is_(None),
                            VideoModel.processing_claimed_at < stale_cutoff,
                        ),
                    )
                ).values(processing_claimed_at=func.now())
            else:
                statement = statement.where(
                    or_(
                        VideoModel.status.in_(("PENDING", "UPLOADED", "FAILED")),
                        and_(
                            VideoModel.status == "PROCESSING",
                            VideoModel.processing_claimed_at < stale_cutoff,
                        ),
                    )
                ).values(
                    status="PROCESSING",
                    failed_stage=None,
                    processing_claimed_at=func.now(),
                )
            result = await session.execute(statement)
            await session.commit()
            return (result.rowcount or 0) == 1

    async def touch_processing(self, video_id: UUID | str) -> None:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            await session.execute(
                update(VideoModel)
                .where(
                    and_(
                        VideoModel.id == normalized_video_id,
                        VideoModel.status.in_(("PROCESSING", "READY")),
                        VideoModel.processing_claimed_at.is_not(None),
                    )
                )
                .values(processing_claimed_at=func.now())
            )
            await session.commit()

    async def release_processing_claim(self, video_id: UUID | str) -> None:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            await session.execute(
                update(VideoModel)
                .where(VideoModel.id == normalized_video_id)
                .values(processing_claimed_at=None)
            )
            await session.commit()

    async def has_fresh_processing_claim(self, video_ids: list[UUID | str]) -> bool:
        normalized_video_ids = self._normalize_uuids(video_ids)
        if not normalized_video_ids:
            return False
        stale_cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self._stale_processing_reclaim_sec
        )
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(VideoModel)
                .where(
                    and_(
                        VideoModel.id.in_(normalized_video_ids),
                        VideoModel.processing_claimed_at >= stale_cutoff,
                    )
                )
            )
            return bool(count)

    async def set_ready(self, video_id: UUID | str) -> None:
        await self.set_status(
            video_id,
            "READY",
            failed_stage=None,
            clear_processing_claim=True,
        )

    async def set_failed(
        self,
        video_id: UUID | str,
        *,
        failed_stage: str,
        error_message: str | None = None,
    ) -> bool:
        del error_message
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            failed_result = await session.execute(
                update(VideoModel)
                .where(
                    and_(
                        VideoModel.id == normalized_video_id,
                        VideoModel.status != "DELETING",
                    )
                )
                .values(
                    status="FAILED",
                    failed_stage=failed_stage,
                    processing_claimed_at=None,
                )
            )
            if (failed_result.rowcount or 0) == 1:
                await session.commit()
                return True
            await session.execute(
                update(VideoModel)
                .where(
                    and_(
                        VideoModel.id == normalized_video_id,
                        VideoModel.status == "DELETING",
                    )
                )
                .values(processing_claimed_at=None)
            )
            await session.commit()
            return False

    async def set_status(
        self,
        video_id: UUID | str,
        status: VideoStatus,
        *,
        failed_stage: str | None = None,
        clear_processing_claim: bool = False,
    ) -> None:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            values = {"status": status, "failed_stage": failed_stage}
            if clear_processing_claim:
                values["processing_claimed_at"] = None
            await session.execute(
                update(VideoModel)
                .where(VideoModel.id == normalized_video_id)
                .values(**values)
            )
            await session.commit()

    async def is_deleting(self, video_id: UUID | str) -> bool:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            status = await session.scalar(select(VideoModel.status).where(VideoModel.id == normalized_video_id))
            return status == "DELETING"

    async def hard_delete_video(self, video_id: UUID | str) -> None:
        await self.hard_delete_videos([video_id])

    async def hard_delete_videos(self, video_ids: list[UUID | str]) -> None:
        normalized_video_ids = self._normalize_uuids(video_ids)
        if not normalized_video_ids:
            return

        async with self._session_factory() as session:
            await session.execute(delete(VideoModel).where(VideoModel.id.in_(normalized_video_ids)))
            await session.commit()

    @staticmethod
    def _to_record(model: VideoModel) -> VideoRecord:
        return VideoRecord(
            id=model.id,
            user_id=model.user_id,
            project_id=model.project_id,
            title=model.title,
            category=model.category,
            input_type=model.input_type,
            source_url=model.source_url,
            storage_path=model.storage_path,
            status=model.status,
            failed_stage=model.failed_stage,
            processing_claimed_at=model.processing_claimed_at,
        )

    @staticmethod
    def _normalize_uuid(value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value
        return UUID(str(value))

    @classmethod
    def _normalize_uuids(cls, values: list[UUID | str]) -> list[UUID]:
        return list(dict.fromkeys(cls._normalize_uuid(value) for value in values))
