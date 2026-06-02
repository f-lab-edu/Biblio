from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.legacy_reindex_store import LegacyReindexStore, ReindexedVectorRecord
from src.infra.db.models import ChunkModel, VideoModel
from src.release.legacy_reindex import EmbeddingBatchPort
from src.release.reembed_text import embedding_input_text


class VideoReembedService:
    """Re-embeds a single video's chunks into a target index using a target model.

    Used after rollback to backfill vectors for videos that were uploaded while
    the bad model was active and are therefore absent from the restored index.
    Does NOT modify chunk rows.
    """

    def __init__(self, *, session: AsyncSession, embedding_client: EmbeddingBatchPort) -> None:
        self._session = session
        self._embedding_client = embedding_client
        self._store = LegacyReindexStore(session)

    async def reembed_video(
        self,
        *,
        video_id: UUID,
        target_model_version: str,
        target_index_name: str,
        trace_id: UUID,
    ) -> int:
        """Embed all chunks of `video_id` into `target_index_name` with `target_model_version`.

        Returns the number of vectors upserted.
        Raises ValueError if the video is missing, any chunk text is empty,
        or the embedding response count does not match the chunk count.
        """
        video = await self._session.get(VideoModel, video_id)
        if video is None:
            raise ValueError(f"video not found: {video_id}")

        chunks = await self._load_chunks(video_id)
        if not chunks:
            return 0

        texts = self._build_embedding_texts(chunks)
        embeddings = await self._embed(texts, trace_id=trace_id, model_version=target_model_version)

        if len(embeddings) != len(chunks):
            raise ValueError(
                f"embedding count mismatch: expected {len(chunks)}, got {len(embeddings)}"
            )

        now = datetime.now(UTC)
        records = [
            ReindexedVectorRecord(
                index_name=target_index_name,
                chunk_id=chunk.id,
                user_id=video.user_id,
                project_id=video.project_id,
                video_id=video.id,
                embedding_vector=embeddings[i],
                embedding_model_version=target_model_version,
                created_at=now,
            )
            for i, chunk in enumerate(chunks)
        ]
        await self._store.upsert_reindexed_vectors(records)
        return len(records)

    async def _load_chunks(self, video_id: UUID) -> list[ChunkModel]:
        result = await self._session.execute(
            select(ChunkModel)
            .where(ChunkModel.video_id == video_id)
            .order_by(ChunkModel.chunk_index.asc().nulls_last(), ChunkModel.id.asc())
        )
        return list(result.scalars().all())

    def _build_embedding_texts(self, chunks: list[ChunkModel]) -> list[str]:
        texts: list[str] = []
        for chunk in chunks:
            text = embedding_input_text(chunk.enriched_text, chunk.text)
            if text is None:
                raise ValueError(f"chunk {chunk.id} has no usable text")
            texts.append(text)
        return texts

    async def _embed(
        self,
        texts: list[str],
        *,
        trace_id: UUID,
        model_version: str,
    ) -> list[list[float]]:
        result = await self._embedding_client.embed_texts(
            texts,
            trace_id=str(trace_id),
            model_version=model_version,
        )
        return result.embeddings
