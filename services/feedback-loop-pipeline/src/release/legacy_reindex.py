from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol
from uuid import UUID, uuid4

from loguru import logger

from src.infra.db.legacy_reindex_store import (
    LegacyReindexStore,
    ReindexChunkRecord,
    ReindexVideoRecord,
    ReindexedVectorRecord,
    VectorIndexCatalogStore,
    ensure_catalog_for_release,
)
from src.infra.db.models import LegacyReindexItemModel
from src.infra.db.stores import ModelReleaseStore
from src.utils.clock import Clock, SystemClock


ITEM_FAILED = "failed"
ITEM_SKIPPED = "skipped"
ITEM_SUCCEEDED = "succeeded"


class EmbeddingBatchPort(Protocol):
    async def embed_texts(
        self,
        texts: list[str],
        *,
        trace_id: str,
        model_version: str | None = None,
    ) -> object: ...


class LegacyReindexLock(Protocol):
    async def try_acquire(self) -> bool: ...

    async def release(self) -> None: ...


class LegacyReindexCoordinatorPort(Protocol):
    async def run_once(self, *, trace_id: UUID) -> object: ...


@dataclass(frozen=True)
class LegacyReindexRunResult:
    status: str
    enqueued_item_count: int = 0
    processed_item_count: int = 0
    succeeded_item_count: int = 0
    failed_item_count: int = 0
    skipped_item_count: int = 0


class LegacyReindexScheduler:
    def __init__(
        self,
        *,
        coordinator: LegacyReindexCoordinatorPort,
        lock: LegacyReindexLock,
        scan_interval_sec: int = 60,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._coordinator = coordinator
        self._lock = lock
        self._scan_interval_sec = scan_interval_sec
        self._sleep = sleep

    async def run_once(self, *, trace_id: UUID) -> object:
        acquired = await self._lock.try_acquire()
        if not acquired:
            return LegacyReindexRunResult(status="lock_busy")
        try:
            return await self._coordinator.run_once(trace_id=trace_id)
        finally:
            await self._lock.release()

    async def run_until_stopped(
        self,
        *,
        stop_event: asyncio.Event,
        trace_id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        while not stop_event.is_set():
            await self.run_once(trace_id=trace_id_factory())
            if not stop_event.is_set():
                await self._sleep(self._scan_interval_sec)


class LegacyReindexCoordinator:
    def __init__(
        self,
        *,
        legacy_store: LegacyReindexStore,
        catalog_store: VectorIndexCatalogStore,
        embedding_client: EmbeddingBatchPort,
        batch_size: int,
        per_run_video_limit: int,
        throttle_sleep_ms: int,
        release_store: ModelReleaseStore | None = None,
        embedding_dimension: int | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._legacy_store = legacy_store
        self._catalog_store = catalog_store
        self._embedding_client = embedding_client
        self._batch_size = batch_size
        self._per_run_video_limit = per_run_video_limit
        self._throttle_sleep_ms = throttle_sleep_ms
        self._release_store = release_store
        self._embedding_dimension = embedding_dimension
        self._clock = clock or SystemClock()

    async def run_once(self, *, trace_id: UUID) -> LegacyReindexRunResult:
        release = await self._current_release()
        if release is None:
            return LegacyReindexRunResult(status="missing_release")
        if release.release_status == "ROLLBACK_PREPARING":
            return LegacyReindexRunResult(status="paused_for_rollback")
        now = self._clock.now()
        if self._embedding_dimension is not None:
            await ensure_catalog_for_release(
                catalog_store=self._catalog_store,
                release=release,
                embedding_dimension=self._embedding_dimension,
                created_at=now,
            )
        enqueued = await self._legacy_store.enqueue_old_only_items(
            target_index_name=release.active_index_name,
            target_model_version=release.active_model_version,
            excluded_index_names=self._excluded_source_indexes(release),
            now=now,
            limit=self._per_run_video_limit,
        )
        items = await self._legacy_store.pending_items(limit=self._per_run_video_limit)

        succeeded = 0
        failed = 0
        skipped = 0
        for item in items:
            item_result = await self._process_item(item, trace_id=trace_id)
            if item_result == ITEM_SUCCEEDED:
                succeeded += 1
            elif item_result == ITEM_SKIPPED:
                skipped += 1
            else:
                failed += 1

        return LegacyReindexRunResult(
            status="processed",
            enqueued_item_count=enqueued,
            processed_item_count=len(items),
            succeeded_item_count=succeeded,
            failed_item_count=failed,
            skipped_item_count=skipped,
        )

    async def _current_release(self):
        if self._release_store is None:
            return None
        return await self._release_store.get_current()

    async def _process_item(self, item: LegacyReindexItemModel, *, trace_id: UUID) -> str:
        now = self._clock.now()
        await self._legacy_store.mark_running(item, started_at=now)

        load_status, video = await self._load_reindexable_video(item)
        if load_status is not None:
            return load_status
        if video is None:
            raise RuntimeError("reindexable video load returned no video")

        texts = await self._embedding_texts_for_video(item=item, video=video)
        if texts is None:
            return ITEM_FAILED

        if not await self._embed_and_upsert_item_batches(item=item, video=video, texts=texts, trace_id=trace_id):
            return ITEM_FAILED

        return await self._complete_item_if_consistent(item=item, video=video, trace_id=trace_id)

    async def _load_reindexable_video(
        self,
        item: LegacyReindexItemModel,
    ) -> tuple[str | None, ReindexVideoRecord | None]:
        video = await self._legacy_store.load_video_for_item(item)
        if video is None:
            await self._legacy_store.mark_failed(
                item,
                failed_stage="TARGET_LOOKUP",
                error_message="video not found",
                failed_at=self._clock.now(),
            )
            return ITEM_FAILED, None
        if video.status == "DELETING":
            await self._legacy_store.mark_skipped(
                item,
                reason="video is deleting",
                skipped_at=self._clock.now(),
            )
            return ITEM_SKIPPED, None
        if video.status != "READY":
            await self._legacy_store.mark_skipped(
                item,
                reason=f"video status is {video.status}",
                skipped_at=self._clock.now(),
            )
            return ITEM_SKIPPED, None
        if not video.chunks:
            await self._legacy_store.mark_failed(
                item,
                failed_stage="CONSISTENCY_CHECK",
                error_message="video has no source chunks",
                failed_at=self._clock.now(),
            )
            return ITEM_FAILED, None
        return None, video

    async def _embedding_texts_for_video(
        self,
        *,
        item: LegacyReindexItemModel,
        video: ReindexVideoRecord,
    ) -> list[str] | None:
        texts = [_embedding_input_text(chunk.enriched_text, chunk.text) for chunk in video.chunks]
        if any(text is None for text in texts):
            await self._legacy_store.mark_failed(
                item,
                failed_stage="TEXT_LOAD",
                error_message="chunk text missing",
                failed_at=self._clock.now(),
            )
            return None
        return [text for text in texts if text is not None]

    async def _embed_and_upsert_item_batches(
        self,
        *,
        item: LegacyReindexItemModel,
        video: ReindexVideoRecord,
        texts: list[str],
        trace_id: UUID,
    ) -> bool:
        for start in range(0, len(video.chunks), self._batch_size):
            chunk_batch = video.chunks[start : start + self._batch_size]
            text_batch = texts[start : start + self._batch_size]
            embeddings = await self._embed_item_batch(item=item, texts=text_batch, trace_id=trace_id)
            if embeddings is None:
                return False
            if not await self._validate_item_batch_embeddings(
                item=item,
                expected_count=len(chunk_batch),
                embeddings=embeddings,
            ):
                return False
            await self._upsert_item_batch_vectors(item=item, video=video, chunk_batch=chunk_batch, embeddings=embeddings)
            await self._throttle_if_needed()
        return True

    async def _embed_item_batch(
        self,
        *,
        item: LegacyReindexItemModel,
        texts: list[str],
        trace_id: UUID,
    ) -> list[list[float]] | None:
        try:
            embedding_batch = await self._embedding_client.embed_texts(
                texts,
                trace_id=str(trace_id),
                model_version=item.target_model_version,
            )
            return _extract_embeddings(embedding_batch)
        except Exception as exc:
            await self._legacy_store.mark_failed(
                item,
                failed_stage="EMBEDDING",
                error_message=str(exc),
                failed_at=self._clock.now(),
            )
            return None

    async def _validate_item_batch_embeddings(
        self,
        *,
        item: LegacyReindexItemModel,
        expected_count: int,
        embeddings: list[list[float]],
    ) -> bool:
        if len(embeddings) != expected_count:
            await self._legacy_store.mark_failed(
                item,
                failed_stage="EMBEDDING",
                error_message="embedding count mismatch",
                failed_at=self._clock.now(),
            )
            return False
        expected_dimension = await self._target_embedding_dimension(item.target_index_name)
        if expected_dimension is not None and any(len(vector) != expected_dimension for vector in embeddings):
            await self._legacy_store.mark_failed(
                item,
                failed_stage="VECTOR_UPSERT",
                error_message="embedding dimension mismatch",
                failed_at=self._clock.now(),
            )
            return False
        return True

    async def _upsert_item_batch_vectors(
        self,
        *,
        item: LegacyReindexItemModel,
        video: ReindexVideoRecord,
        chunk_batch: list[ReindexChunkRecord],
        embeddings: list[list[float]],
    ) -> None:
        await self._legacy_store.upsert_reindexed_vectors(
            [
                ReindexedVectorRecord(
                    index_name=item.target_index_name,
                    chunk_id=chunk.chunk_id,
                    user_id=video.user_id,
                    project_id=video.project_id,
                    video_id=video.video_id,
                    embedding_vector=embeddings[index],
                    embedding_model_version=item.target_model_version,
                    created_at=chunk.source_created_at,
                )
                for index, chunk in enumerate(chunk_batch)
            ]
        )

    async def _complete_item_if_consistent(
        self,
        *,
        item: LegacyReindexItemModel,
        video: ReindexVideoRecord,
        trace_id: UUID,
    ) -> str:
        target_count = await self._legacy_store.target_vector_count(item=item)
        if target_count != len(video.chunks):
            await self._legacy_store.mark_failed(
                item,
                failed_stage="CONSISTENCY_CHECK",
                error_message="target index missing chunk vectors",
                failed_at=self._clock.now(),
            )
            return ITEM_FAILED
        await self._legacy_store.mark_succeeded(
            item,
            total_chunk_count=len(video.chunks),
            completed_at=self._clock.now(),
        )
        logger.bind(
            trace_id=str(trace_id),
            video_id=str(item.video_id),
            source_index_name=item.source_index_name,
            target_index_name=item.target_index_name,
        ).info("legacy_reindex.item succeeded")
        return ITEM_SUCCEEDED

    async def _target_embedding_dimension(self, index_name: str) -> int | None:
        catalog = await self._catalog_store.get(index_name)
        return catalog.embedding_dimension if catalog is not None else None

    async def _throttle_if_needed(self) -> None:
        if self._throttle_sleep_ms > 0:
            await asyncio.sleep(self._throttle_sleep_ms / 1000)

    @staticmethod
    def _excluded_source_indexes(release) -> set[str]:
        excluded = {release.active_index_name}
        if release.candidate_index_name is not None:
            excluded.add(release.candidate_index_name)
        return excluded


def _embedding_input_text(enriched_text: str | None, text: str | None) -> str | None:
    if enriched_text is not None and enriched_text.strip():
        return enriched_text
    if text is not None and text.strip():
        return text
    return None


def _extract_embeddings(embedding_batch: object) -> list[list[float]]:
    embeddings = getattr(embedding_batch, "embeddings", None)
    if not isinstance(embeddings, list):
        raise TypeError("embedding batch must expose an embeddings list")
    return embeddings
