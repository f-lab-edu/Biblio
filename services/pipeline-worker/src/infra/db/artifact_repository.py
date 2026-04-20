from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import AssetModel, ChunkModel, TranscriptSegmentModel, VectorIndexEntryModel, VideoModel


@dataclass(slots=True)
class AssetRecord:
    asset_type: str
    storage_path: str
    start_ms: int | None = None
    end_ms: int | None = None
    id: UUID | str | None = None


@dataclass(slots=True)
class TranscriptSegmentRecord:
    segment_index: int
    text: str
    start_ms: int
    end_ms: int
    stt_model_version: str
    id: UUID | str | None = None


@dataclass(slots=True)
class ChunkRecord:
    chunk_index: int
    text: str
    enriched_text: str
    start_ms: int
    end_ms: int
    chunking_version: str
    stt_model_version: str
    embedding_model_version: str
    visual_caption: str = ""
    ocr_text: str = ""
    scene_tags: str = ""
    keyframe_asset_id: UUID | str | None = None
    id: UUID | str | None = None


class ArtifactRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_asset(self, video_id: UUID | str, asset: AssetRecord) -> UUID:
        normalized_video_id = self._normalize_uuid(video_id)
        asset_id = self._normalize_uuid(asset.id) if asset.id is not None else uuid4()
        async with self._session_factory() as session:
            session.add(
                AssetModel(
                    id=asset_id,
                    video_id=normalized_video_id,
                    asset_type=asset.asset_type,
                    storage_path=asset.storage_path,
                    start_ms=asset.start_ms,
                    end_ms=asset.end_ms,
                )
            )
            await session.commit()
        return asset_id

    async def upsert_asset(self, video_id: UUID | str, asset: AssetRecord) -> UUID:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(AssetModel).where(
                        and_(
                            AssetModel.video_id == normalized_video_id,
                            AssetModel.storage_path == asset.storage_path,
                        )
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.start_ms = asset.start_ms
                existing.end_ms = asset.end_ms
                await session.commit()
                return existing.id
            asset_id = self._normalize_uuid(asset.id) if asset.id is not None else uuid4()
            session.add(
                AssetModel(
                    id=asset_id,
                    video_id=normalized_video_id,
                    asset_type=asset.asset_type,
                    storage_path=asset.storage_path,
                    start_ms=asset.start_ms,
                    end_ms=asset.end_ms,
                )
            )
            await session.commit()
        return asset_id

    async def list_assets(self, video_id: UUID | str, *, asset_type: str | None = None) -> list[AssetRecord]:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            query = select(AssetModel).where(AssetModel.video_id == normalized_video_id)
            if asset_type is not None:
                query = query.where(AssetModel.asset_type == asset_type)
            result = await session.execute(query.order_by(AssetModel.storage_path.asc()))
            return [
                AssetRecord(
                    id=model.id,
                    asset_type=model.asset_type,
                    storage_path=model.storage_path,
                    start_ms=model.start_ms,
                    end_ms=model.end_ms,
                )
                for model in result.scalars().all()
            ]

    async def get_audio_asset(self, video_id: UUID | str) -> AssetRecord | None:
        assets = await self.list_assets(video_id, asset_type="AUDIO")
        return assets[0] if assets else None

    async def replace_transcripts(
        self,
        video_id: UUID | str,
        *,
        stt_model_version: str,
        segments: list[TranscriptSegmentRecord],
    ) -> None:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            await session.execute(
                delete(TranscriptSegmentModel).where(
                    and_(
                        TranscriptSegmentModel.video_id == normalized_video_id,
                        TranscriptSegmentModel.stt_model_version == stt_model_version,
                    )
                )
            )
            for segment in segments:
                session.add(
                    TranscriptSegmentModel(
                        id=self._normalize_uuid(segment.id) if segment.id is not None else uuid4(),
                        video_id=normalized_video_id,
                        segment_index=segment.segment_index,
                        text=segment.text,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        stt_model_version=stt_model_version,
                    )
                )
            await session.commit()

    async def load_transcripts(
        self,
        video_id: UUID | str,
        *,
        stt_model_version: str,
    ) -> list[TranscriptSegmentRecord]:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            result = await session.execute(
                select(TranscriptSegmentModel)
                .where(
                    and_(
                        TranscriptSegmentModel.video_id == normalized_video_id,
                        TranscriptSegmentModel.stt_model_version == stt_model_version,
                    )
                )
                .order_by(TranscriptSegmentModel.segment_index.asc())
            )
            return [
                TranscriptSegmentRecord(
                    id=model.id,
                    segment_index=model.segment_index,
                    text=model.text,
                    start_ms=model.start_ms,
                    end_ms=model.end_ms,
                    stt_model_version=model.stt_model_version,
                )
                for model in result.scalars().all()
            ]

    async def persist_chunks_and_vectors(
        self,
        video_id: UUID | str,
        *,
        chunks: list[ChunkRecord],
        embeddings: list[list[float]],
        set_ready: bool,
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("Chunk and embedding counts must match")

        if not chunks:
            raise ValueError("At least one chunk is required")

        normalized_video_id = self._normalize_uuid(video_id)
        stt_model_version = chunks[0].stt_model_version
        embedding_model_version = chunks[0].embedding_model_version

        async with self._session_factory() as session:
            owner_id = await self._load_video_owner_id(session, normalized_video_id)
            existing_chunk_ids = (
                await session.execute(
                    select(ChunkModel.id).where(
                        and_(
                            ChunkModel.video_id == normalized_video_id,
                            ChunkModel.stt_model_version == stt_model_version,
                            ChunkModel.embedding_model_version == embedding_model_version,
                        )
                    )
                )
            ).scalars().all()
            if existing_chunk_ids:
                await session.execute(
                    delete(VectorIndexEntryModel).where(VectorIndexEntryModel.chunk_id.in_(existing_chunk_ids))
                )
                await session.execute(delete(ChunkModel).where(ChunkModel.id.in_(existing_chunk_ids)))

            for chunk, embedding in zip(chunks, embeddings, strict=True):
                chunk_id = self._normalize_uuid(chunk.id) if chunk.id is not None else uuid4()
                session.add(
                    ChunkModel(
                        id=chunk_id,
                        video_id=normalized_video_id,
                        chunk_index=chunk.chunk_index,
                        text=chunk.text,
                        enriched_text=chunk.enriched_text,
                        start_ms=chunk.start_ms,
                        end_ms=chunk.end_ms,
                        keyframe_asset_id=(
                            self._normalize_uuid(chunk.keyframe_asset_id)
                            if chunk.keyframe_asset_id is not None
                            else None
                        ),
                        chunking_version=chunk.chunking_version,
                        stt_model_version=chunk.stt_model_version,
                        embedding_model_version=chunk.embedding_model_version,
                        visual_caption=chunk.visual_caption,
                        ocr_text=chunk.ocr_text,
                        scene_tags=chunk.scene_tags,
                    )
                )
                session.add(
                    VectorIndexEntryModel(
                        chunk_id=chunk_id,
                        user_id=owner_id,
                        video_id=normalized_video_id,
                        embedding_vector=embedding,
                        embedding_model_version=chunk.embedding_model_version,
                    )
                )

            if set_ready:
                await session.execute(
                    update(VideoModel).where(VideoModel.id == normalized_video_id).values(status="READY", failed_stage=None)
                )

            await session.commit()

    async def delete_video_artifacts(self, video_id: UUID | str) -> list[str]:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            asset_paths = (
                await session.execute(select(AssetModel.storage_path).where(AssetModel.video_id == normalized_video_id))
            ).scalars().all()
            chunk_ids = (
                await session.execute(select(ChunkModel.id).where(ChunkModel.video_id == normalized_video_id))
            ).scalars().all()
            if chunk_ids:
                await session.execute(delete(VectorIndexEntryModel).where(VectorIndexEntryModel.chunk_id.in_(chunk_ids)))
            await session.execute(delete(ChunkModel).where(ChunkModel.video_id == normalized_video_id))
            await session.execute(delete(TranscriptSegmentModel).where(TranscriptSegmentModel.video_id == normalized_video_id))
            await session.execute(delete(AssetModel).where(AssetModel.video_id == normalized_video_id))
            await session.commit()
            return list(asset_paths)

    async def list_chunks(self, video_id: UUID | str) -> list[ChunkRecord]:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            result = await session.execute(
                select(ChunkModel)
                .where(ChunkModel.video_id == normalized_video_id)
                .order_by(ChunkModel.chunk_index.asc())
            )
            return [
                ChunkRecord(
                    id=model.id,
                    chunk_index=model.chunk_index,
                    text=model.text,
                    enriched_text=model.enriched_text,
                    start_ms=model.start_ms,
                    end_ms=model.end_ms,
                    keyframe_asset_id=model.keyframe_asset_id,
                    chunking_version=model.chunking_version,
                    stt_model_version=model.stt_model_version,
                    embedding_model_version=model.embedding_model_version,
                    visual_caption=model.visual_caption,
                    ocr_text=model.ocr_text,
                    scene_tags=model.scene_tags,
                )
                for model in result.scalars().all()
            ]

    async def list_vectors(self, video_id: UUID | str) -> list[list[float]]:
        normalized_video_id = self._normalize_uuid(video_id)
        async with self._session_factory() as session:
            result = await session.execute(
                select(VectorIndexEntryModel.embedding_vector).where(
                    VectorIndexEntryModel.video_id == normalized_video_id
                )
            )
            return list(result.scalars().all())

    @staticmethod
    async def _load_video_owner_id(session: AsyncSession, video_id: UUID) -> UUID:
        owner_id = await session.scalar(
            select(VideoModel.user_id).where(VideoModel.id == video_id)
        )
        if owner_id is None:
            raise ValueError(f"Video not found for vector persistence: {video_id}")
        return owner_id

    @staticmethod
    def _normalize_uuid(value: UUID | str) -> UUID:
        if isinstance(value, UUID):
            return value
        return UUID(str(value))
