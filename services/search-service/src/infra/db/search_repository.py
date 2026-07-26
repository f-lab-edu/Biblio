"""Repository for search queries and response snapshot writes.

Provides: corpus readiness check, FTS, ANN, SOT gate, SearchResponseSnapshot sink.
"""

from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Literal
from uuid import UUID

from sqlalchemy import exists, not_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from src.common.logging import info as log_info
from src.common.logging import warning as log_warning
from src.common.observability import SearchRequestContext, elapsed_ms
from src.infra.db.models import (
    ChunkModel,
    ModelReleaseModel,
    ProjectModel,
    SearchConversationModel,
    SearchResponseSnapshotModel,
    VideoModel,
)

DEFAULT_VECTOR_INDEX_NAME = "default-index"

DB_CONNECTION_ACQUIRE_LOG = "search.db_connection_acquire"

DbOperation = Literal[
    "readiness",
    "fts",
    "ann",
    "sot_gate",
    "snapshot",
    "conversation",
]


async def _acquire_connection_for_observation(
    session: AsyncSession,
    *,
    request_context: SearchRequestContext | None,
    db_operation: DbOperation,
    target_role: str | None = None,
    model_version: str | None = None,
    index_name: str | None = None,
) -> None:
    """Take the DB connection early so the wait can be timed on its own.

    Without a request context this is a no-op, keeping the original lazy
    connection behaviour for direct callers such as startup and tests.
    The measured span covers pool queue wait plus `pool_pre_ping` and any
    stale-connection reconnect, so it is not pure queue wait time.
    """
    if request_context is None:
        return

    fields: dict[str, object] = {
        **request_context.as_log_fields(),
        "db_operation": db_operation,
        "target_role": target_role,
        "model_version": model_version,
        "index_name": index_name,
    }
    started_at = perf_counter()
    try:
        await session.connection()
    except Exception as exc:
        log_warning(
            DB_CONNECTION_ACQUIRE_LOG,
            **fields,
            status="failed",
            db_connection_acquire_ms=elapsed_ms(started_at),
            error_type=type(exc).__name__,
        )
        raise
    log_info(
        DB_CONNECTION_ACQUIRE_LOG,
        **fields,
        status="success",
        db_connection_acquire_ms=elapsed_ms(started_at),
    )


PROJECT_SERVING_GATE_SQL = """
(
    p.search_serving_state = 'SERVABLE'
    AND p.lifecycle_state = 'ACTIVE'
    AND NOT EXISTS (
        SELECT 1
        FROM video project_video
        WHERE project_video.project_id = p.id
          AND project_video.status != 'READY'
    )
)
"""


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


@dataclass(frozen=True, slots=True)
class ServingSearchTarget:
    model_version: str
    index_name: str


@dataclass(frozen=True, slots=True)
class ServingSearchTargets:
    active: ServingSearchTarget
    previous: ServingSearchTarget | None = None

    @property
    def target_entries(self) -> list[tuple[str, ServingSearchTarget]]:
        entries = [("active", self.active)]
        if self.previous is not None:
            entries.append(("previous", self.previous))
        return entries

    @property
    def served_vector_paths(self) -> list[dict[str, str]]:
        return [
            {
                "role": role,
                "model_version": target.model_version,
                "index_name": target.index_name,
            }
            for role, target in self.target_entries
        ]


@dataclass(frozen=True, slots=True)
class SearchResponseSnapshotWrite:
    req_id: UUID
    user_id: UUID
    project_id: UUID
    query_text: str
    topk_chunk_ids: list[str]
    used_chunk_ids: list[str]
    active_model_version: str
    active_index_name: str
    served_vector_paths: list[dict[str, str]]
    project_serving_state: str
    expires_at: datetime
    scope_notice: str | None = None


@dataclass(frozen=True, slots=True)
class SearchConversationWrite:
    req_id: UUID
    user_id: UUID
    project_id: UUID
    query: str
    answer: str
    sources: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class SearchConversationRecord:
    req_id: UUID
    user_id: UUID
    project_id: UUID
    query: str
    answer: str
    sources: list[dict[str, object]]
    created_at: datetime


class SearchRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_serving_search_targets(self) -> ServingSearchTargets | None:
        """Read active and optional previous search targets from ModelRelease SOT."""
        async with self._session_factory() as session:
            stmt = select(
                ModelReleaseModel.active_model_version,
                ModelReleaseModel.active_index_name,
                ModelReleaseModel.previous_model_version,
                ModelReleaseModel.previous_index_name,
            ).where(ModelReleaseModel.singleton_key == 1)
            result = await session.execute(stmt)
            row = result.one_or_none()
            if row is None:
                return None
            active = ServingSearchTarget(
                model_version=row.active_model_version,
                index_name=row.active_index_name,
            )
            previous = None
            if row.previous_model_version and row.previous_index_name:
                previous = ServingSearchTarget(
                    model_version=row.previous_model_version,
                    index_name=row.previous_index_name,
                )
            return ServingSearchTargets(active=active, previous=previous)


    async def check_corpus_readiness(
        self,
        user_id: UUID,
        project_id: UUID,
        *,
        request_context: SearchRequestContext | None = None,
    ) -> CorpusReadiness:
        """Single-project readiness check: total video count + non-ready count."""
        async with self._session_factory() as session:
            stmt = text("""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE v.status != 'READY') AS non_ready
                FROM video v
                JOIN project p ON v.project_id = p.id
                WHERE v.user_id = :user_id
                  AND p.user_id = :user_id
                  AND v.project_id = :project_id
                  AND p.lifecycle_state = 'ACTIVE'
            """)
            await _acquire_connection_for_observation(
                session,
                request_context=request_context,
                db_operation="readiness",
            )
            result = await session.execute(
                stmt, {"user_id": user_id, "project_id": project_id}
            )
            row = result.one()
            return CorpusReadiness(
                total_videos=row.total,
                non_ready_count=row.non_ready,
            )

    async def fts_search(
        self,
        user_id: UUID,
        project_id: UUID,
        query: str,
        *,
        top_k: int,
        request_context: SearchRequestContext | None = None,
    ) -> list[FTSCandidate]:
        """FTS keyword search on chunk text with user tenancy via video join.

        Uses PostgreSQL ts_rank + to_tsvector/to_tsquery for ranking.
        FTS target text is COALESCE(enriched_text, text) per SPEC §2.2.
        Project-scoped chunks are served only when the project is `SERVABLE`
        and every video in that project is `READY`.
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
                JOIN project p ON v.project_id = p.id
                WHERE v.user_id = :user_id
                  AND v.project_id = :project_id
                  AND p.user_id = :user_id
                  AND v.status = 'READY'
                  AND """ + PROJECT_SERVING_GATE_SQL + """
                  AND to_tsvector('simple', COALESCE(c.enriched_text, c.text))
                      @@ plainto_tsquery('simple', :query)
                ORDER BY rank
                LIMIT :top_k
            """)
            await _acquire_connection_for_observation(
                session,
                request_context=request_context,
                db_operation="fts",
            )
            result = await session.execute(
                stmt,
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "query": query,
                    "top_k": top_k,
                },
            )
            return [
                FTSCandidate(chunk_id=row.chunk_id, rank=row.rank)
                for row in result
            ]

    async def ann_search(
        self,
        user_id: UUID,
        project_id: UUID,
        query_embedding: list[float],
        index_name: str = DEFAULT_VECTOR_INDEX_NAME,
        *,
        top_k: int,
        request_context: SearchRequestContext | None = None,
        target_role: str | None = None,
        model_version: str | None = None,
    ) -> list[ANNCandidate]:
        """ANN vector search on vector_index_entry with READY video filter.

        Uses cosine distance via pgvector <=> operator.
        Joins video table to exclude non-READY videos from candidates.
        """
        async with self._session_factory() as session:
            stmt = text("""
                SELECT vie.chunk_id,
                       ROW_NUMBER() OVER (
                           ORDER BY vie.embedding_vector <=> CAST(:query_embedding AS vector)
                       ) AS rank
                FROM vector_index_entry vie
                JOIN video v ON vie.video_id = v.id
                JOIN project p ON v.project_id = p.id
                WHERE vie.user_id = :user_id
                  AND vie.project_id = :project_id
                  AND v.project_id = :project_id
                  AND p.user_id = :user_id
                  AND vie.index_name = :index_name
                  AND vie.project_id IS NOT DISTINCT FROM v.project_id
                  AND v.status = 'READY'
                  AND """ + PROJECT_SERVING_GATE_SQL + """
                ORDER BY vie.embedding_vector <=> CAST(:query_embedding AS vector)
                LIMIT :top_k
            """)
            await _acquire_connection_for_observation(
                session,
                request_context=request_context,
                db_operation="ann",
                target_role=target_role,
                model_version=model_version,
                index_name=index_name,
            )
            result = await session.execute(
                stmt,
                {
                    "user_id": user_id,
                    "project_id": project_id,
                    "index_name": index_name,
                    "query_embedding": str(query_embedding),
                    "top_k": top_k,
                },
            )
            return [
                ANNCandidate(chunk_id=row.chunk_id, rank=row.rank)
                for row in result
            ]

    async def sot_gate(
        self,
        user_id: UUID,
        project_id: UUID,
        chunk_ids: list[UUID],
        *,
        request_context: SearchRequestContext | None = None,
    ) -> list[ChunkRecord]:
        """SOT serving gate: verify chunks belong to a servable READY project.

        Returns only chunks that pass the gate, with full metadata for response.
        """
        if not chunk_ids:
            return []

        async with self._session_factory() as session:
            project_video = aliased(VideoModel)
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
                .join(ProjectModel, VideoModel.project_id == ProjectModel.id)
                .where(
                    ChunkModel.id.in_(chunk_ids),
                    VideoModel.user_id == user_id,
                    VideoModel.project_id == project_id,
                    VideoModel.status == "READY",
                    ProjectModel.user_id == user_id,
                    ProjectModel.search_serving_state == "SERVABLE",
                    ProjectModel.lifecycle_state == "ACTIVE",
                    not_(
                        exists()
                        .where(project_video.project_id == ProjectModel.id)
                        .where(project_video.status != "READY")
                    ),
                )
            )
            await _acquire_connection_for_observation(
                session,
                request_context=request_context,
                db_operation="sot_gate",
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

    async def save_search_response_snapshot(
        self,
        snapshot: SearchResponseSnapshotWrite,
        *,
        request_context: SearchRequestContext | None = None,
    ) -> None:
        """Persist search response context for later feedback attribution."""
        async with self._session_factory() as session:
            session.add(
                SearchResponseSnapshotModel(
                    req_id=snapshot.req_id,
                    user_id=snapshot.user_id,
                    project_id=snapshot.project_id,
                    query_text=snapshot.query_text,
                    topk_chunk_ids=snapshot.topk_chunk_ids,
                    used_chunk_ids=snapshot.used_chunk_ids,
                    active_model_version=snapshot.active_model_version,
                    active_index_name=snapshot.active_index_name,
                    served_vector_paths=snapshot.served_vector_paths,
                    project_serving_state=snapshot.project_serving_state,
                    scope_notice=snapshot.scope_notice,
                    expires_at=snapshot.expires_at,
                )
            )
            await _acquire_connection_for_observation(
                session,
                request_context=request_context,
                db_operation="snapshot",
            )
            await session.commit()

    async def save_conversation(
        self,
        conversation: SearchConversationWrite,
        *,
        request_context: SearchRequestContext | None = None,
    ) -> None:
        """Persist a frozen project search conversation turn."""
        async with self._session_factory() as session:
            session.add(
                SearchConversationModel(
                    req_id=conversation.req_id,
                    user_id=conversation.user_id,
                    project_id=conversation.project_id,
                    query=conversation.query,
                    answer=conversation.answer,
                    sources=conversation.sources,
                )
            )
            await _acquire_connection_for_observation(
                session,
                request_context=request_context,
                db_operation="conversation",
            )
            await session.commit()

    async def list_conversations_for_project(
        self, user_id: UUID, project_id: UUID
    ) -> list[SearchConversationRecord]:
        """Return frozen conversation turns for one user's project."""
        async with self._session_factory() as session:
            stmt = (
                select(SearchConversationModel)
                .where(
                    SearchConversationModel.user_id == user_id,
                    SearchConversationModel.project_id == project_id,
                )
                .order_by(
                    SearchConversationModel.created_at.asc(),
                    SearchConversationModel.req_id.asc(),
                )
            )
            result = await session.execute(stmt)
            return [
                SearchConversationRecord(
                    req_id=row.req_id,
                    user_id=row.user_id,
                    project_id=row.project_id,
                    query=row.query,
                    answer=row.answer,
                    sources=row.sources,
                    created_at=row.created_at,
                )
                for row in result.scalars()
            ]
