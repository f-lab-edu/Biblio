from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.infra.db.models import (
    ChunkModel,
    LegacyReindexItemModel,
    ModelReleaseModel,
    VectorIndexCatalogModel,
    VectorIndexEntryModel,
    VideoModel,
)


@dataclass(frozen=True)
class LegacyCutoverGateResult:
    blocked: bool
    remaining_video_count: int


@dataclass(frozen=True)
class ReindexChunkRecord:
    chunk_id: UUID
    text: str | None
    enriched_text: str | None
    source_created_at: datetime


@dataclass(frozen=True)
class ReindexVideoRecord:
    video_id: UUID
    user_id: UUID
    project_id: UUID | None
    status: str
    chunks: list[ReindexChunkRecord]


@dataclass(frozen=True)
class ReindexedVectorRecord:
    index_name: str
    chunk_id: UUID
    user_id: UUID
    project_id: UUID | None
    video_id: UUID
    embedding_vector: list[float]
    embedding_model_version: str
    created_at: datetime


class VectorIndexCatalogStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_index(
        self,
        *,
        index_name: str,
        model_version: str,
        embedding_dimension: int,
        created_at: datetime,
    ) -> None:
        existing = await self._session.get(VectorIndexCatalogModel, index_name)
        if existing is None:
            self._session.add(
                VectorIndexCatalogModel(
                    index_name=index_name,
                    model_version=model_version,
                    embedding_dimension=embedding_dimension,
                    created_at=created_at,
                )
            )
            await self._session.flush()
            return
        existing.model_version = model_version
        existing.embedding_dimension = embedding_dimension
        await self._session.flush()

    async def get(self, index_name: str) -> VectorIndexCatalogModel | None:
        return await self._session.get(VectorIndexCatalogModel, index_name)

# legacy reindex 작업용 DB 저장소 클래스
#   1. cutover 전에 legacy reindex가 남았는지 확인
#   2. 재색인해야 할 video를 legacy_reindex_item에 등록 (LegacyReindexItem.status == pending)
#   3. PENDING인 item을 가져옴 
#   4. item 상태를 RUNNING / SUCCEEDED / FAILED / SKIPPED로 변경
#   5. item에 해당하는 video + chunk를 로드
#   6. 새로 만든 embedding vector를 target index에 upsert
#   7. target index에 vector가 다 들어갔는지 개수 확인
class LegacyReindexStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure_cutover_ready(
        self,
        *,
        active_index_name: str,
        active_model_version: str,
        candidate_index_name: str | None,
        now: datetime,
        limit: int = 100,
    ) -> LegacyCutoverGateResult:
        remaining_count = await self.count_old_only_videos(
            target_index_name=active_index_name,
            excluded_index_names={active_index_name, candidate_index_name} - {None},
        )
        if remaining_count == 0:
            return LegacyCutoverGateResult(blocked=False, remaining_video_count=0)
        await self.enqueue_old_only_items(
            target_index_name=active_index_name,
            target_model_version=active_model_version,
            excluded_index_names={active_index_name, candidate_index_name} - {None},
            now=now,
            limit=limit,
        )
        return LegacyCutoverGateResult(blocked=True, remaining_video_count=remaining_count)

    async def enqueue_old_only_items(
        self,
        *,
        target_index_name: str,
        target_model_version: str,
        excluded_index_names: set[str],
        now: datetime,
        limit: int,
    ) -> int:
        enqueued_count = 0
        for source_index in await self._source_indexes(excluded_index_names):
            if enqueued_count >= limit:
                break
            remaining_limit = limit - enqueued_count
            rows = await self._old_only_video_rows(
                source_index_name=source_index.index_name,
                target_index_name=target_index_name,
                limit=remaining_limit,
            )
            for row in rows:
                created = await self._ensure_item(
                    video_id=row.video_id,
                    user_id=row.user_id,
                    project_id=row.project_id,
                    source_index_name=source_index.index_name,
                    source_model_version=source_index.model_version,
                    target_index_name=target_index_name,
                    target_model_version=target_model_version,
                    now=now,
                )
                if created:
                    enqueued_count += 1
        await self._session.flush()
        return enqueued_count

    async def pending_items(self, *, limit: int) -> list[LegacyReindexItemModel]:
        result = await self._session.execute(
            select(LegacyReindexItemModel)
            .where(LegacyReindexItemModel.status == "PENDING")
            .order_by(LegacyReindexItemModel.updated_at.asc(), LegacyReindexItemModel.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_running(self, item: LegacyReindexItemModel, *, started_at: datetime) -> None:
        item.status = "RUNNING"
        item.started_at = started_at
        item.updated_at = started_at
        item.failed_stage = None
        item.failure_type = None
        item.last_error = None
        await self._session.flush()

    async def mark_succeeded(
        self,
        item: LegacyReindexItemModel,
        *,
        total_chunk_count: int,
        completed_at: datetime,
    ) -> None:
        item.status = "SUCCEEDED"
        item.total_chunk_count = total_chunk_count
        item.completed_chunk_count = total_chunk_count
        item.completed_at = completed_at
        item.updated_at = completed_at
        item.failed_stage = None
        item.failure_type = None
        item.last_error = None
        await self._session.flush()

    async def mark_failed(
        self,
        item: LegacyReindexItemModel,
        *,
        failed_stage: str,
        error_message: str,
        failed_at: datetime,
    ) -> None:
        item.status = "FAILED"
        item.failed_stage = failed_stage
        item.failure_type = "ERROR"
        item.last_error = error_message
        item.retry_count += 1
        item.updated_at = failed_at
        await self._session.flush()

    async def mark_skipped(
        self,
        item: LegacyReindexItemModel,
        *,
        reason: str,
        skipped_at: datetime,
    ) -> None:
        item.status = "SKIPPED"
        item.failed_stage = None
        item.failure_type = None
        item.last_error = reason
        item.completed_at = skipped_at
        item.updated_at = skipped_at
        await self._session.flush()

    async def load_video_for_item(self, item: LegacyReindexItemModel) -> ReindexVideoRecord | None:
        video = await self._session.get(VideoModel, item.video_id)
        if video is None:
            return None
        source_entry = aliased(VectorIndexEntryModel)
        result = await self._session.execute(
            select(
                ChunkModel.id,
                ChunkModel.text,
                ChunkModel.enriched_text,
                source_entry.created_at,
            )
            .join(
                source_entry,
                and_(
                    source_entry.chunk_id == ChunkModel.id,
                    source_entry.index_name == item.source_index_name,
                ),
            )
            .where(ChunkModel.video_id == item.video_id)
            .order_by(ChunkModel.chunk_index.asc().nulls_last(), ChunkModel.id.asc())
        )
        chunks = [
            ReindexChunkRecord(
                chunk_id=row.id,
                text=row.text,
                enriched_text=row.enriched_text,
                source_created_at=row.created_at,
            )
            for row in result.all()
        ]
        return ReindexVideoRecord(
            video_id=video.id,
            user_id=video.user_id,
            project_id=video.project_id,
            status=video.status,
            chunks=chunks,
        )

    async def upsert_reindexed_vectors(self, vectors: list[ReindexedVectorRecord]) -> None:
        if not vectors:
            return
        insert_factory = postgres_insert if self._session.get_bind().dialect.name == "postgresql" else sqlite_insert
        for vector in vectors:
            stmt = insert_factory(VectorIndexEntryModel).values(
                index_name=vector.index_name,
                chunk_id=vector.chunk_id,
                user_id=vector.user_id,
                project_id=vector.project_id,
                video_id=vector.video_id,
                embedding_vector=vector.embedding_vector,
                embedding_model_version=vector.embedding_model_version,
                created_at=vector.created_at,
            )
            update_values = {
                "user_id": stmt.excluded.user_id,
                "project_id": stmt.excluded.project_id,
                "video_id": stmt.excluded.video_id,
                "embedding_vector": stmt.excluded.embedding_vector,
                "embedding_model_version": stmt.excluded.embedding_model_version,
            }
            await self._session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["index_name", "chunk_id"],
                    set_=update_values,
                )
            )
        await self._session.flush()

    async def target_vector_count(self, *, item: LegacyReindexItemModel) -> int:
        chunk_ids = (
            await self._session.execute(
                select(ChunkModel.id).where(ChunkModel.video_id == item.video_id)
            )
        ).scalars().all()
        if not chunk_ids:
            return 0
        count = await self._session.scalar(
            select(func.count())
            .select_from(VectorIndexEntryModel)
            .where(
                VectorIndexEntryModel.index_name == item.target_index_name,
                VectorIndexEntryModel.chunk_id.in_(chunk_ids),
            )
        )
        return int(count or 0)

    async def count_old_only_videos(
        self,
        *,
        target_index_name: str,
        excluded_index_names: set[str],
    ) -> int:
        counts = [
            len(
                await self._old_only_video_rows(
                    source_index_name=source_index.index_name,
                    target_index_name=target_index_name,
                    limit=None,
                )
            )
            for source_index in await self._source_indexes(excluded_index_names)
        ]
        return sum(counts)

    async def _source_indexes(self, excluded_index_names: set[str]) -> list[VectorIndexCatalogModel]:
        result = await self._session.execute(
            select(VectorIndexCatalogModel)
            .where(
                VectorIndexCatalogModel.index_name.not_in(excluded_index_names),
                VectorIndexCatalogModel.deleted_at.is_(None),
            )
            .order_by(VectorIndexCatalogModel.created_at.asc(), VectorIndexCatalogModel.index_name.asc())
        )
        return list(result.scalars().all())

    async def _old_only_video_rows(
        self,
        *,
        source_index_name: str,
        target_index_name: str,
        limit: int | None,
    ) -> list[object]:
        source_entry = aliased(VectorIndexEntryModel)
        target_entry = aliased(VectorIndexEntryModel)
        target_row_exists = (
            select(target_entry.chunk_id)
            .where(
                target_entry.chunk_id == source_entry.chunk_id,
                target_entry.index_name == target_index_name,
            )
            .exists()
        )
        stmt = (
            select(
                source_entry.video_id.label("video_id"),
                source_entry.user_id.label("user_id"),
                source_entry.project_id.label("project_id"),
            )
            .join(VideoModel, VideoModel.id == source_entry.video_id)
            .where(
                source_entry.index_name == source_index_name,
                VideoModel.status == "READY",
                ~target_row_exists,
            )
            .group_by(source_entry.video_id, source_entry.user_id, source_entry.project_id)
            .order_by(func.min(source_entry.created_at).asc(), source_entry.video_id.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt)
        return list(result.all())

    async def _ensure_item(
        self,
        *,
        video_id: UUID,
        user_id: UUID,
        project_id: UUID | None,
        source_index_name: str,
        source_model_version: str,
        target_index_name: str,
        target_model_version: str,
        now: datetime,
    ) -> bool:
        existing = await self._session.scalar(
            select(LegacyReindexItemModel).where(
                LegacyReindexItemModel.video_id == video_id,
                LegacyReindexItemModel.source_index_name == source_index_name,
                LegacyReindexItemModel.target_index_name == target_index_name,
            )
        )
        if existing is not None:
            if existing.status in {"SUCCEEDED", "SKIPPED"}:
                existing.status = "PENDING"
                existing.completed_at = None
                existing.failed_stage = None
                existing.failure_type = None
                existing.last_error = None
                existing.source_model_version = source_model_version
                existing.target_model_version = target_model_version
                existing.updated_at = now
            return False
        self._session.add(
            LegacyReindexItemModel(
                video_id=video_id,
                user_id=user_id,
                project_id=project_id,
                source_index_name=source_index_name,
                source_model_version=source_model_version,
                target_index_name=target_index_name,
                target_model_version=target_model_version,
                status="PENDING",
                retry_count=0,
                total_chunk_count=0,
                completed_chunk_count=0,
                created_at=now,
                updated_at=now,
            )
        )
        return True


async def ensure_catalog_for_release(
    *,
    catalog_store: VectorIndexCatalogStore,
    release: ModelReleaseModel,
    embedding_dimension: int,
    created_at: datetime,
) -> None:
    await catalog_store.upsert_index(
        index_name=release.active_index_name,
        model_version=release.active_model_version,
        embedding_dimension=embedding_dimension,
        created_at=created_at,
    )
    if release.previous_index_name is not None and release.previous_model_version is not None:
        await catalog_store.upsert_index(
            index_name=release.previous_index_name,
            model_version=release.previous_model_version,
            embedding_dimension=embedding_dimension,
            created_at=created_at,
        )
    if release.candidate_index_name is not None and release.candidate_model_version is not None:
        await catalog_store.upsert_index(
            index_name=release.candidate_index_name,
            model_version=release.candidate_model_version,
            embedding_dimension=embedding_dimension,
            created_at=created_at,
        )
