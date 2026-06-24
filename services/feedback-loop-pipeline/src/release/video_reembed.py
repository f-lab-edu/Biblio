from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.legacy_reindex_store import (
    LegacyReindexStore,
    ReindexedVectorRecord,
    VectorIndexCatalogStore,
)
from src.infra.db.models import ChunkModel, VideoModel
from src.release.legacy_reindex import EmbeddingBatchPort
from src.release.reembed_text import embedding_input_text


DEFAULT_REEMBED_BATCH_SIZE = 8


class VideoReembedService:
    """Re-embeds a single video's chunks into a target index using a target model.

    Used after rollback to backfill vectors for videos that were uploaded while
    the bad model was active and are therefore absent from the restored index.
    Does NOT modify chunk rows.
    """

    def __init__(
        self,
        *,
        session: AsyncSession,
        embedding_client: EmbeddingBatchPort,
        batch_size: int = DEFAULT_REEMBED_BATCH_SIZE,
    ) -> None:
        self._session = session
        self._embedding_client = embedding_client
        self._batch_size = batch_size
        self._store = LegacyReindexStore(session)
        self._catalog_store = VectorIndexCatalogStore(session)

    async def reembed_video(
        self,
        *,
        video_id: UUID,
        target_model_version: str,
        target_index_name: str,
        trace_id: UUID,
    ) -> int:
        """Embed all chunks of `video_id` into `target_index_name` with `target_model_version`.

        Chunks are embedded and upserted in batches of `batch_size`.
        Returns the number of vectors upserted.
        Raises ValueError if the video is missing, any chunk text is empty, the
        embedding response count does not match the batch size, or an embedding
        dimension does not match the target index catalog.
        """
        video = await self._session.get(VideoModel, video_id)
        if video is None:
            raise ValueError(f"video not found: {video_id}")

        chunks = await self._load_chunks(video_id)
        if not chunks:
            return 0

        texts = self._build_embedding_texts(chunks)
        expected_dimension = await self._target_embedding_dimension(target_index_name)

        upserted = 0
        for start in range(0, len(chunks), self._batch_size):
            chunk_batch = chunks[start : start + self._batch_size]
            text_batch = texts[start : start + self._batch_size]
            upserted += await self._embed_and_upsert_batch(
                video=video,
                chunk_batch=chunk_batch,
                text_batch=text_batch,
                target_model_version=target_model_version,
                target_index_name=target_index_name,
                expected_dimension=expected_dimension,
                trace_id=trace_id,
            )
        return upserted

    async def _embed_and_upsert_batch(
        self,
        *,
        video: VideoModel,
        chunk_batch: list[ChunkModel],
        text_batch: list[str],
        target_model_version: str,
        target_index_name: str,
        expected_dimension: int | None,
        trace_id: UUID,
    ) -> int:
        embeddings = await self._embed(
            text_batch, trace_id=trace_id, model_version=target_model_version
        )
        self._validate_batch_embeddings(
            embeddings=embeddings,
            expected_count=len(chunk_batch),
            expected_dimension=expected_dimension,
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
            for i, chunk in enumerate(chunk_batch)
        ]
        await self._store.upsert_reindexed_vectors(records)
        return len(records)

    @staticmethod
    def _validate_batch_embeddings(
        *,
        embeddings: list[list[float]],
        expected_count: int,
        expected_dimension: int | None,
    ) -> None:
        if len(embeddings) != expected_count:
            raise ValueError(
                f"embedding count mismatch: expected {expected_count}, got {len(embeddings)}"
            )
        if expected_dimension is not None and any(
            len(vector) != expected_dimension for vector in embeddings
        ):
            raise ValueError(
                f"embedding dimension mismatch: expected {expected_dimension}"
            )

    async def _target_embedding_dimension(self, index_name: str) -> int | None:
        catalog = await self._catalog_store.get(index_name)
        return catalog.embedding_dimension if catalog is not None else None

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
