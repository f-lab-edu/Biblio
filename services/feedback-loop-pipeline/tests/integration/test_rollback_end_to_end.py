"""End-to-end integration test for the full rollback recovery flow.

Covers:
  1. Seed:        registry v1→v2, STABLE release at v2, project with v2-only chunk.
  2. Rollback:    RollbackTransitionManager restores release to v1; project becomes ROLLBACK_EXCLUDED.
  3. Legacy pause: LegacyReindexCoordinator pauses while a ROLLBACK_EXCLUDED project exists.
  4. Recovery dispatch: RollbackRecoveryAdapter enqueues REEMBEDDING_REQUEST for the video.
  5. Reembed:     VideoReembedService writes the index-v1 vector entry for the chunk.
  6. Servable return: second scan_and_recover flips project back to SERVABLE.
  7. Legacy resumes: no ROLLBACK_EXCLUDED projects → coordinator no longer pauses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.infra.db.legacy_reindex_store import LegacyReindexStore, VectorIndexCatalogStore
from src.infra.db.models import (
    Base,
    ChunkModel,
    ModelReleaseModel,
    ModelSnapshotModel,
    ProjectModel,
    VectorIndexEntryModel,
    VideoModel,
)
from src.infra.db.snapshot_registry import ModelSnapshotStore
from src.infra.db.stores import ModelReleaseStore, ProjectRollbackStore
from src.release.legacy_reindex import LegacyReindexCoordinator
from src.release.rollback import (
    AlwaysReadyRollbackTarget,
    ImmediateIndexRestore,
    RollbackRequestMessage,
    RollbackTransitionManager,
)
from src.release.video_reembed import VideoReembedService
from src.runtime.queue import InMemoryBrokerClient


class _StubEmbedder:
    async def embed_texts(self, texts, *, trace_id, model_version=None):
        class _R:
            embeddings = [[0.1, 0.2] for _ in texts]

        return _R()


class _FakeSettings:
    feedback_reembedding_queue_name: str = "feedback.reembedding"


def _build_legacy_coordinator(session: AsyncSession) -> LegacyReindexCoordinator:
    return LegacyReindexCoordinator(
        legacy_store=LegacyReindexStore(session),
        catalog_store=VectorIndexCatalogStore(session),
        embedding_client=_StubEmbedder(),
        batch_size=8,
        per_run_video_limit=100,
        throttle_sleep_ms=0,
        release_store=ModelReleaseStore(session),
        project_store=ProjectRollbackStore(session),
    )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with factory() as db_session:
            yield db_session
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_full_rollback_recovery_flow(session: AsyncSession) -> None:
    # ------------------------------------------------------------------
    # Step 1 — Seed
    # ------------------------------------------------------------------
    switched_at = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    user_id = uuid4()

    # Registry: v1 cutover first, then v2 → ACTIVE=v2, PREVIOUS_STABLE=v1
    snapshot_store = ModelSnapshotStore(session)
    await snapshot_store.record_cutover(
        model_version="v1",
        index_name="index-v1",
        captured_at=datetime(2026, 6, 1, 9, 0, tzinfo=UTC),
    )
    await snapshot_store.record_cutover(
        model_version="v2",
        index_name="index-v2",
        captured_at=datetime(2026, 6, 1, 9, 30, tzinfo=UTC),
    )

    # ModelRelease: STABLE, active=v2, switched_at matching the rollback message
    session.add(
        ModelReleaseModel(
            release_status="STABLE",
            active_model_version="v2",
            active_index_name="index-v2",
            previous_model_version="v1",
            previous_index_name="index-v1",
            switched_at=switched_at,
        )
    )
    await session.flush()

    # Project with one READY video and one chunk at v2 (→ v2-only, no index-v1 entry)
    project = ProjectModel(user_id=user_id, title="Test project")
    session.add(project)
    await session.flush()

    video = VideoModel(
        user_id=user_id,
        project_id=project.id,
        title="Test video",
        status="READY",
    )
    session.add(video)
    await session.flush()

    chunk = ChunkModel(
        video_id=video.id,
        text="v2 chunk text",
        embedding_model_version="v2",
    )
    session.add(chunk)
    await session.flush()

    # VectorIndexEntry exists only in index-v2 (v2-only)
    session.add(
        VectorIndexEntryModel(
            index_name="index-v2",
            chunk_id=chunk.id,
            user_id=user_id,
            project_id=project.id,
            video_id=video.id,
            embedding_model_version="v2",
            created_at=switched_at,
        )
    )
    await session.flush()

    # Confirm seed: registry shows v2 ACTIVE, v1 PREVIOUS_STABLE
    snapshot_rows = {
        r.model_version: r.status
        for r in (await session.execute(select(ModelSnapshotModel))).scalars().all()
    }
    assert snapshot_rows == {"v1": "PREVIOUS_STABLE", "v2": "ACTIVE"}
    assert project.search_serving_state == "SERVABLE"

    # ------------------------------------------------------------------
    # Step 2 — Rollback
    # ------------------------------------------------------------------
    manager = RollbackTransitionManager(
        release_store=ModelReleaseStore(session),
        project_store=ProjectRollbackStore(session),
        target_readiness=AlwaysReadyRollbackTarget(),
        index_restore=ImmediateIndexRestore(),
    )

    rollback_result = await manager.handle_request(
        RollbackRequestMessage(
            message_type="ROLLBACK_REQUEST",
            payload_version="v1",
            trace_id=uuid4(),
            attempt=1,
            issued_at=switched_at,
            expected_active_model_version="v2",
            expected_switched_at=switched_at,
        )
    )

    assert rollback_result.status == "restored"

    release = await ModelReleaseStore(session).get_current()
    assert release is not None
    assert release.active_model_version == "v1"

    await session.refresh(project)
    assert project.search_serving_state == "ROLLBACK_EXCLUDED"

    snapshot_rows = {
        r.model_version: r.status
        for r in (await session.execute(select(ModelSnapshotModel))).scalars().all()
    }
    assert snapshot_rows == {"v2": "ROLLED_BACK", "v1": "ACTIVE"}

    # ------------------------------------------------------------------
    # Step 3 — Legacy reindex pauses during rollback recovery
    # ------------------------------------------------------------------
    legacy_result = await _build_legacy_coordinator(session).run_once(trace_id=uuid4())
    assert legacy_result.status == "paused_for_rollback"

    # ------------------------------------------------------------------
    # Step 4 — Recovery dispatch enqueues REEMBEDDING_REQUEST
    # ------------------------------------------------------------------
    from src.bootstrap import RollbackRecoveryAdapter

    broker = InMemoryBrokerClient()
    adapter = RollbackRecoveryAdapter(session, broker, _FakeSettings())
    await adapter.scan_and_recover()

    messages = await broker.consume(_FakeSettings.feedback_reembedding_queue_name, limit=10)
    assert len(messages) >= 1
    payloads = [m.payload for m in messages]
    assert any(
        p.get("message_type") == "REEMBEDDING_REQUEST" and str(video.id) in str(p)
        for p in payloads
    ), f"No REEMBEDDING_REQUEST for video {video.id} in {payloads}"

    # Project must still be ROLLBACK_EXCLUDED — worker hasn't finished yet
    await session.refresh(project)
    assert project.search_serving_state == "ROLLBACK_EXCLUDED"

    # ------------------------------------------------------------------
    # Step 5 — Reembed completes: writes index-v1 vector entry
    # ------------------------------------------------------------------
    reembed_count = await VideoReembedService(
        session=session, embedding_client=_StubEmbedder()
    ).reembed_video(
        video_id=video.id,
        target_model_version="v1",
        target_index_name="index-v1",
        trace_id=uuid4(),
    )
    assert reembed_count >= 1

    # Verify the index-v1 entry now exists
    entry = await session.get(VectorIndexEntryModel, ("index-v1", chunk.id))
    assert entry is not None
    assert entry.embedding_model_version == "v1"

    # ------------------------------------------------------------------
    # Step 6 — Second scan_and_recover flips project back to SERVABLE
    # ------------------------------------------------------------------
    broker2 = InMemoryBrokerClient()
    adapter2 = RollbackRecoveryAdapter(session, broker2, _FakeSettings())
    await adapter2.scan_and_recover()

    await session.refresh(project)
    assert project.search_serving_state == "SERVABLE"

    # ------------------------------------------------------------------
    # Step 7 — Legacy reindex resumes (no ROLLBACK_EXCLUDED projects)
    # ------------------------------------------------------------------
    legacy_result_after = await _build_legacy_coordinator(session).run_once(trace_id=uuid4())
    assert legacy_result_after.status != "paused_for_rollback"
