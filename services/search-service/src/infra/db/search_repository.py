"""Read-only repository for search queries.

Provides: corpus readiness check, FTS, ANN, SOT gate.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.models import ChunkModel, VectorIndexEntryModel, VideoModel


@dataclass(slots=True)
class CorpusReadiness:
    """Result of the single-query readiness check."""

    total_videos: int
    non_ready_count: int


@dataclass(slots=True)
class ChunkRecord:
    """Immutable read result from SOT gate."""

    chunk_id: UUID
    video_id: UUID
    title: str
    text: str
    enriched_text: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class FTSCandidate:
    chunk_id: UUID
    rank: int


@dataclass(slots=True)
class ANNCandidate:
    chunk_id: UUID
    rank: int


class SearchRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def check_corpus_readiness(self, user_id: UUID) -> CorpusReadiness:
        """Single-query readiness check: total video count + non-ready count."""
        async with self._session_factory() as session:
            stmt = text("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE status != 'READY') AS non_ready
                FROM video
                WHERE user_id = :user_id
            """)
            result = await session.execute(stmt, {"user_id": user_id})
            row = result.one()
            return CorpusReadiness(
                total_videos=row.total,
                non_ready_count=row.non_ready,
            )

    async def fts_search(
        self, user_id: UUID, query: str, *, top_k: int
    ) -> list[FTSCandidate]:
        """FTS keyword search on chunk text with user tenancy via video join.

        Uses PostgreSQL ts_rank + to_tsvector/to_tsquery for ranking.
        FTS target text is COALESCE(enriched_text, text) per SPEC §2.2.
        """
        async with self._session_factory() as session:
            stmt = text("""
                SELECT c.id AS chunk_id,
                       ROW_NUMBER() OVER (
                           ORDER BY ts_rank(
                               to_tsvector('simple', COALESCE(c.enriched_text, c.text)),
                               plainto_tsquery('simple', :query)
                           ) DESC
                       ) AS rank
                FROM chunk c
                JOIN video v ON c.video_id = v.id
                WHERE v.user_id = :user_id
                  AND v.status = 'READY'
                  AND to_tsvector('simple', COALESCE(c.enriched_text, c.text))
                      @@ plainto_tsquery('simple', :query)
                ORDER BY rank
                LIMIT :top_k
            """)
            result = await session.execute(
                stmt, {"user_id": user_id, "query": query, "top_k": top_k}
            )
            return [
                FTSCandidate(chunk_id=row.chunk_id, rank=row.rank)
                for row in result
            ]

    async def ann_search(
        self, user_id: UUID, query_embedding: list[float], *, top_k: int
    ) -> list[ANNCandidate]:
        """ANN vector search on vector_index_entry with READY video filter.

        Uses cosine distance via pgvector <=> operator.
        Joins video table to exclude non-READY videos from candidates.
        """
        async with self._session_factory() as session:
            stmt = text("""
                SELECT vie.chunk_id,
                       ROW_NUMBER() OVER (
                           ORDER BY vie.embedding_vector <=> :query_embedding::vector
                       ) AS rank
                FROM vector_index_entry vie
                JOIN video v ON vie.video_id = v.id
                WHERE vie.user_id = :user_id
                  AND v.status = 'READY'
                ORDER BY vie.embedding_vector <=> :query_embedding::vector
                LIMIT :top_k
            """)
            result = await session.execute(
                stmt,
                {
                    "user_id": user_id,
                    "query_embedding": str(query_embedding),
                    "top_k": top_k,
                },
            )
            return [
                ANNCandidate(chunk_id=row.chunk_id, rank=row.rank)
                for row in result
            ]

    async def sot_gate(
        self, user_id: UUID, chunk_ids: list[UUID]
    ) -> list[ChunkRecord]:
        """SOT serving gate: verify chunks belong to READY videos owned by user.

        Returns only chunks that pass the gate, with full metadata for response.
        """
        if not chunk_ids:
            return []

        async with self._session_factory() as session:
            stmt = (
                select(
                    ChunkModel.id.label("chunk_id"),
                    ChunkModel.video_id,
                    VideoModel.title,
                    ChunkModel.text,
                    ChunkModel.enriched_text,
                    ChunkModel.start_ms,
                    ChunkModel.end_ms,
                )
                .join(VideoModel, ChunkModel.video_id == VideoModel.id)
                .where(
                    ChunkModel.id.in_(chunk_ids),
                    VideoModel.user_id == user_id,
                    VideoModel.status == "READY",
                )
            )
            result = await session.execute(stmt)
            return [
                ChunkRecord(
                    chunk_id=row.chunk_id,
                    video_id=row.video_id,
                    title=row.title,
                    text=row.text,
                    enriched_text=row.enriched_text,
                    start_ms=row.start_ms,
                    end_ms=row.end_ms,
                )
                for row in result
            ]
