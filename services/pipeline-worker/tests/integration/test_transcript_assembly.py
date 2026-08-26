import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from src.infra.ai.google_stt_adapter import TranscriptWordDTO
from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineFrameCandidateModel,
    PipelineRunModel,
    TranscriptSegmentModel,
    VideoModel,
)
from src.infra.db.release_repository import EmbeddingTarget
from src.infra.db.transcript_assembly import TranscriptAssemblyCoordinator
from src.infra.storage.inmemory_storage import InMemoryStorageClient
from src.services.chunking_service import ChunkingService
from src.services.transcript_assembly_service import TranscriptAssemblyService
from src.services.transcript_merge_service import TranscriptMergeService
from src.services.transcription_artifact import TranscriptionArtifact


class _TargetProvider:
    async def get_online_ingest_target(
        self,
        *,
        fallback_model_version: str,
        fallback_index_name: str = "default-index",
    ) -> EmbeddingTarget:
        return EmbeddingTarget(
            index_name=fallback_index_name,
            model_version=fallback_model_version,
        )


def _coordinator(session_factory, storage) -> TranscriptAssemblyCoordinator:
    return TranscriptAssemblyCoordinator(
        session_factory=session_factory,
        storage=storage,
        service=TranscriptAssemblyService(
            merge_service=TranscriptMergeService(),
            chunking_service=ChunkingService(
                max_tokens=2,
                overlap_sentences=1,
            ),
        ),
        target_provider=_TargetProvider(),
        fallback_embedding_model_version="embed-v1",
    )


async def _seed_run(session_factory, storage):
    video_id = uuid4()
    run_id = uuid4()
    part_ranges = [(0, 10_000), (5_000, 15_000), (10_000, 20_000)]
    part_ids = [uuid4() for _ in part_ranges]
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="assembly test",
                    category="test",
                    input_type="FILE",
                    storage_path="videos/source.mp4",
                    status="PROCESSING",
                )
            )
            session.add(
                PipelineRunModel(
                    id=run_id,
                    video_id=video_id,
                    pipeline_version="pipeline-v1",
                    normalization_status="COMPLETED",
                    normalization_completed=True,
                    total_part_count=3,
                )
            )
            for index, ((start_ms, end_ms), part_id) in enumerate(
                zip(part_ranges, part_ids, strict=True)
            ):
                session.add(
                    PipelineAudioPartModel(
                        audio_part_id=part_id,
                        pipeline_run_id=run_id,
                        part_index=index,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        audio_gcs_path=f"audio/part-{index}.flac",
                        stt_model_version="chirp_3",
                        status="RUNNING",
                        attempt_count=1,
                        message_id=40 + index,
                        started_at=datetime.now(UTC),
                    )
                )
            for index, timestamp_ms in enumerate((5_000, 15_000)):
                session.add(
                    PipelineFrameCandidateModel(
                        frame_candidate_id=uuid4(),
                        pipeline_run_id=run_id,
                        frame_index=index,
                        timestamp_ms=timestamp_ms,
                        frame_gcs_path=f"frames/frame-{index}.jpg",
                    )
                )
    return video_id, run_id, part_ids, part_ranges


async def _complete_part(
    session_factory,
    storage,
    *,
    run_id,
    part_id,
    part_index,
    time_range,
    word,
) -> None:
    result_ref = f"results/part-{part_index}.json"
    artifact = TranscriptionArtifact(
        pipeline_run_id=run_id,
        audio_part_id=part_id,
        part_index=part_index,
        start_ms=time_range[0],
        end_ms=time_range[1],
        stt_model_version="chirp_3",
        segments=(),
        words=(word,),
    )
    storage.objects[result_ref] = artifact.to_bytes()
    async with session_factory() as session:
        async with session.begin():
            part = await session.get(PipelineAudioPartModel, part_id)
            assert part is not None
            part.status = "COMPLETED"
            part.result_ref = result_ref


@pytest.mark.asyncio
async def test_out_of_order_parts_advance_only_contiguous_cursor(session_factory) -> None:
    storage = InMemoryStorageClient()
    video_id, run_id, part_ids, ranges = await _seed_run(session_factory, storage)
    coordinator = _coordinator(session_factory, storage)

    await _complete_part(
        session_factory,
        storage,
        run_id=run_id,
        part_id=part_ids[0],
        part_index=0,
        time_range=ranges[0],
        word=TranscriptWordDTO("zero.", 1_000, 2_000),
    )
    first = await asyncio.wait_for(
        coordinator.advance(pipeline_run_id=run_id, trace_id=uuid4()),
        timeout=3,
    )

    await _complete_part(
        session_factory,
        storage,
        run_id=run_id,
        part_id=part_ids[2],
        part_index=2,
        time_range=ranges[2],
        word=TranscriptWordDTO("two.", 4_000, 5_000),
    )
    blocked = await asyncio.wait_for(
        coordinator.advance(pipeline_run_id=run_id, trace_id=uuid4()),
        timeout=3,
    )

    await _complete_part(
        session_factory,
        storage,
        run_id=run_id,
        part_id=part_ids[1],
        part_index=1,
        time_range=ranges[1],
        word=TranscriptWordDTO("one.", 4_000, 5_000),
    )
    final = await asyncio.wait_for(
        coordinator.advance(pipeline_run_id=run_id, trace_id=uuid4()),
        timeout=3,
    )
    duplicate = await asyncio.wait_for(
        coordinator.advance(pipeline_run_id=run_id, trace_id=uuid4()),
        timeout=3,
    )

    async with session_factory() as session:
        run = await session.get(PipelineRunModel, run_id)
        segments = list(
            (
                await session.scalars(
                    select(TranscriptSegmentModel)
                    .where(TranscriptSegmentModel.video_id == video_id)
                    .order_by(TranscriptSegmentModel.segment_index)
                )
            ).all()
        )
        chunks = list(
            (
                await session.scalars(
                    select(PipelineChunkWorkModel)
                    .where(PipelineChunkWorkModel.pipeline_run_id == run_id)
                    .order_by(PipelineChunkWorkModel.chunk_index)
                )
            ).all()
        )

    assert first.parts_applied == 1
    assert blocked.reason == "inactive_or_no_contiguous_parts"
    assert final.parts_applied == 2
    assert duplicate.advanced is False
    assert run is not None
    assert run.next_part_index == 3
    assert run.assembly_completed is True
    assert [segment.text for segment in segments] == ["zero.", "one.", "two."]
    assert [chunk.enrichment_status for chunk in chunks] == ["READY", "READY"]
    assert [chunk.frame_ref for chunk in chunks] == [
        "frames/frame-0.jpg",
        "frames/frame-1.jpg",
    ]


@pytest.mark.asyncio
async def test_uncontended_assembly_loads_artifacts_once(
    session_factory,
    monkeypatch,
) -> None:
    storage = InMemoryStorageClient()
    _, run_id, part_ids, ranges = await _seed_run(session_factory, storage)
    coordinator = _coordinator(session_factory, storage)
    load_artifacts_original = coordinator._load_artifacts
    load_count = 0

    async def load_artifacts(parts):
        nonlocal load_count
        load_count += 1
        return await load_artifacts_original(parts)

    monkeypatch.setattr(coordinator, "_load_artifacts", load_artifacts)
    await _complete_part(
        session_factory,
        storage,
        run_id=run_id,
        part_id=part_ids[0],
        part_index=0,
        time_range=ranges[0],
        word=TranscriptWordDTO("zero.", 1_000, 2_000),
    )

    decision = await coordinator.advance(pipeline_run_id=run_id, trace_id=uuid4())

    assert decision.reason == "advanced"
    assert load_count == 1


@pytest.mark.asyncio
async def test_cursor_conflict_reloads_latest_snapshot_and_completes(
    session_factory,
    monkeypatch,
) -> None:
    storage = InMemoryStorageClient()
    _, run_id, part_ids, ranges = await _seed_run(session_factory, storage)
    coordinator_a = _coordinator(session_factory, storage)
    coordinator_b = _coordinator(session_factory, storage)
    a_persist_ready = asyncio.Event()
    b_persist_ready = asyncio.Event()
    release_a = asyncio.Event()
    release_b = asyncio.Event()
    persist_a_original = coordinator_a._persist
    persist_b_original = coordinator_b._persist

    async def persist_a(snapshot, progress, target, work_id):
        a_persist_ready.set()
        await release_a.wait()
        return await persist_a_original(snapshot, progress, target, work_id)

    async def persist_b(snapshot, progress, target, work_id):
        b_persist_ready.set()
        await release_b.wait()
        return await persist_b_original(snapshot, progress, target, work_id)

    monkeypatch.setattr(coordinator_a, "_persist", persist_a)
    monkeypatch.setattr(coordinator_b, "_persist", persist_b)

    await _complete_part(
        session_factory,
        storage,
        run_id=run_id,
        part_id=part_ids[0],
        part_index=0,
        time_range=ranges[0],
        word=TranscriptWordDTO("zero.", 1_000, 2_000),
    )
    task_a = asyncio.create_task(
        coordinator_a.advance(pipeline_run_id=run_id, trace_id=uuid4())
    )
    await asyncio.wait_for(a_persist_ready.wait(), timeout=3)

    for part_index in (1, 2):
        await _complete_part(
            session_factory,
            storage,
            run_id=run_id,
            part_id=part_ids[part_index],
            part_index=part_index,
            time_range=ranges[part_index],
            word=TranscriptWordDTO(f"{part_index}.", 4_000, 5_000),
        )
    task_b = asyncio.create_task(
        coordinator_b.advance(pipeline_run_id=run_id, trace_id=uuid4())
    )
    await asyncio.wait_for(b_persist_ready.wait(), timeout=3)

    release_a.set()
    first = await asyncio.wait_for(task_a, timeout=3)
    release_b.set()
    final = await asyncio.wait_for(task_b, timeout=3)

    async with session_factory() as session:
        run = await session.get(PipelineRunModel, run_id)
        chunks = list(
            (
                await session.scalars(
                    select(PipelineChunkWorkModel)
                    .where(PipelineChunkWorkModel.pipeline_run_id == run_id)
                    .order_by(PipelineChunkWorkModel.chunk_index)
                )
            ).all()
        )

    assert first.reason == "advanced"
    assert first.parts_applied == 1
    assert final.reason == "completed"
    assert final.parts_applied == 2
    assert run is not None
    assert run.next_part_index == run.total_part_count == 3
    assert run.assembly_completed is True
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert chunks
    assert all(chunk.enrichment_status == "READY" for chunk in chunks)
    assert all(chunk.frame_ref is not None for chunk in chunks)


@pytest.mark.asyncio
async def test_deleting_video_does_not_advance_assembly_cursor(session_factory) -> None:
    storage = InMemoryStorageClient()
    video_id, run_id, part_ids, ranges = await _seed_run(session_factory, storage)
    await _complete_part(
        session_factory,
        storage,
        run_id=run_id,
        part_id=part_ids[0],
        part_index=0,
        time_range=ranges[0],
        word=TranscriptWordDTO("zero.", 1_000, 2_000),
    )
    async with session_factory() as session:
        async with session.begin():
            video = await session.get(VideoModel, video_id)
            assert video is not None
            video.status = "DELETING"

    decision = await asyncio.wait_for(
        _coordinator(session_factory, storage).advance(
            pipeline_run_id=run_id,
            trace_id=uuid4(),
        ),
        timeout=3,
    )

    async with session_factory() as session:
        run = await session.get(PipelineRunModel, run_id)
        chunk_count = await session.scalar(
            select(func.count(PipelineChunkWorkModel.chunk_work_id)).where(
                PipelineChunkWorkModel.pipeline_run_id == run_id
            )
        )
    assert decision.advanced is False
    assert run is not None and run.next_part_index == 0
    assert chunk_count == 0
