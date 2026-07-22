from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from loguru import logger

from src.infra.ai.vision_adapter import MockVisionAdapter
from src.infra.db.video_repository import VideoRecord
from src.infra.media.youtube_downloader import DownloadError, InMemoryYoutubeDownloader
from src.services.chunking_service import ChunkingService
from src.services.pipeline_errors import AudioPreparationError
from src.services.pipeline_orchestrator import PipelineOrchestrator
from src.usecases.delete_video import DeleteVideoUseCase
from src.usecases.process_video import ProcessVideoUseCase
from src.utils.workdir import WorkdirManager
from tests.support import build_embedding_client, build_ffmpeg_adapter, build_stt_adapter


@pytest.mark.asyncio
async def test_process_video_happy_path(
    video_repository,
    process_video_use_case,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-1",
    )

    assert result.action == "processed"
    assert len(await artifact_repository.list_chunks(video_id)) > 0
    assert (await video_repository.get_video(video_id)).status == "READY"


@pytest.mark.asyncio
async def test_process_video_skips_ready_same_version(
    video_repository,
    process_video_use_case,
    artifact_repository,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="READY")
    )
    await artifact_repository.persist_chunks_and_vectors(
        video_id,
        chunks=[
            __import__("src.infra.db.artifact_repository", fromlist=["ChunkRecord"]).ChunkRecord(
                chunk_index=0,
                text="text",
                enriched_text="text",
                start_ms=0,
                end_ms=1,
                chunking_version="v1",
                stt_model_version="chirp_2",
                embedding_model_version="v001",
            )
        ],
        embeddings=[[1.0]],
        set_ready=True,
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-2",
    )

    assert result.action == "skip"


@pytest.mark.asyncio
async def test_process_video_claims_ready_video_when_outputs_need_rebuild(
    video_repository,
    process_video_use_case,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/ready-source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/ready-source.mp4",
            status="READY",
        )
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-ready-rebuild",
    )

    rebuilt_video = await video_repository.get_video(video_id)
    assert result.action == "processed"
    assert rebuilt_video.status == "READY"
    assert rebuilt_video.processing_claimed_at is None


@pytest.mark.asyncio
async def test_process_video_fails_on_missing_storage_object(
    video_repository,
    process_video_use_case,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    # storage_path DB에 있지만 storage_client에는 없음 → download에서 FileNotFoundError
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/missing.mp4", status="UPLOADED")
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id=str(trace_id),
    )

    assert result.action == "failed"
    assert result.failed_stage == "DOWNLOAD"
    video = await video_repository.get_video(video_id)
    assert video.status == "FAILED"
    assert video.failed_stage == "DOWNLOAD"
    assert video.failure_code == "SOURCE_UNAVAILABLE"
    assert video.failure_trace_id == trace_id


@pytest.mark.asyncio
async def test_process_video_fails_on_embedding_exhausted(
    video_repository,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    ffmpeg_client, _ = build_ffmpeg_adapter()
    # max_retries=2 → 최대 3회 시도. fail_embed_times=3 이면 모두 실패하여 ExternalAIAdapterError(UNAVAILABLE) 발생
    orchestrator = PipelineOrchestrator(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        storage_client=storage_client,
        youtube_downloader=InMemoryYoutubeDownloader(),
        ffmpeg_client=ffmpeg_client,
        stt_adapter=build_stt_adapter(),
        embedding_client=build_embedding_client(fail_embed_times=3),
        vision_adapter=MockVisionAdapter(caption="caption"),
        workdir_manager=WorkdirManager(base_dir=Path.cwd()),
        chunking_service=ChunkingService(max_tokens=6, overlap_sentences=1),
        embedding_batch_size=2,
        stt_model_version="chirp_2",
        embedding_model_version="v001",
    )
    use_case = ProcessVideoUseCase(
        video_repository=video_repository,
        orchestrator=orchestrator,
        delete_video_use_case=DeleteVideoUseCase(
            video_repository=video_repository,
            artifact_repository=artifact_repository,
            storage_client=storage_client,
        ),
        stt_model_version="chirp_2",
        embedding_model_version="v001",
    )

    result = await use_case.execute(
        video_id=video_id,
        trace_id=str(trace_id),
    )

    assert result.action == "failed"
    assert result.failed_stage == "EMBEDDING"
    video = await video_repository.get_video(video_id)
    assert video.status == "FAILED"
    assert video.failed_stage == "EMBEDDING"
    assert video.failure_code == "EMBEDDING_FAILED"
    assert video.failure_trace_id == trace_id


@pytest.mark.asyncio
async def test_process_video_fails_on_stt_exhausted(
    video_repository,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    ffmpeg_client, _ = build_ffmpeg_adapter()
    orchestrator = PipelineOrchestrator(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        storage_client=storage_client,
        youtube_downloader=InMemoryYoutubeDownloader(),
        ffmpeg_client=ffmpeg_client,
        stt_adapter=build_stt_adapter(fail_submit_times=3),
        embedding_client=build_embedding_client(),
        vision_adapter=MockVisionAdapter(caption="caption"),
        workdir_manager=WorkdirManager(base_dir=Path.cwd()),
        chunking_service=ChunkingService(max_tokens=6, overlap_sentences=1),
        embedding_batch_size=2,
        stt_model_version="chirp_2",
        embedding_model_version="v001",
    )
    use_case = ProcessVideoUseCase(
        video_repository=video_repository,
        orchestrator=orchestrator,
        delete_video_use_case=DeleteVideoUseCase(
            video_repository=video_repository,
            artifact_repository=artifact_repository,
            storage_client=storage_client,
        ),
        stt_model_version="chirp_2",
        embedding_model_version="v001",
    )

    result = await use_case.execute(
        video_id=video_id,
        trace_id=str(trace_id),
    )

    assert result.action == "failed"
    assert result.failed_stage == "STT"
    video = await video_repository.get_video(video_id)
    assert video.status == "FAILED"
    assert video.failed_stage == "STT"
    assert video.failure_code == "STT_FAILED"
    assert video.failure_trace_id == trace_id


@pytest.mark.asyncio
async def test_process_video_claim_conflict_skips(
    video_repository,
    process_video_use_case,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )
    # 다른 워커가 먼저 claim → PROCESSING 상태. claim_processing은 PENDING/UPLOADED만 허용하므로 0 rows 반환
    await video_repository.set_status(video_id, "PROCESSING")

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-conflict",
    )

    assert result.action == "skip"
    # 외부 API 호출 없이 skip → 상태는 변경되지 않아야 함
    assert (await video_repository.get_video(video_id)).status == "PROCESSING"


@pytest.mark.asyncio
async def test_process_video_maps_download_error_to_download_stage(
    video_repository,
    process_video_use_case,
    youtube_downloader,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    source_url = "https://youtu.be/private"
    youtube_downloader.errors[source_url] = DownloadError(
        "Sign in to confirm you're not a bot",
        category="youtube_block",
    )
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            input_type="EXTERNAL_URL",
            source_url=source_url,
            storage_path="videos/external/original",
            status="PENDING",
        )
    )

    failure_records = []
    sink_id = logger.add(
        lambda message: failure_records.append(message.record),
        filter=lambda record: record["message"].startswith("video.processing.failed"),
    )
    try:
        result = await process_video_use_case.execute(
            video_id=video_id,
            trace_id=str(trace_id),
        )
    finally:
        logger.remove(sink_id)

    video = await video_repository.get_video(video_id)
    assert result.action == "failed"
    assert result.failed_stage == "DOWNLOAD"
    assert video.status == "FAILED"
    assert video.failed_stage == "DOWNLOAD"
    assert video.failure_code == "YOUTUBE_BLOCKED"
    assert video.failure_trace_id == trace_id
    assert len(failure_records) == 1
    assert failure_records[0]["extra"] == {
        "video_id": video_id,
        "trace_id": str(trace_id),
        "failed_stage": "DOWNLOAD",
        "failure_code": "YOUTUBE_BLOCKED",
        "provider": "youtube",
    }


@pytest.mark.asyncio
async def test_process_video_maps_source_limit_failure_to_extract_stage(
    video_repository,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    storage_client.objects["videos/source.mp4"] = b"too-large"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/source.mp4",
            status="UPLOADED",
        )
    )
    ffmpeg_client, _ = build_ffmpeg_adapter()
    orchestrator = PipelineOrchestrator(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        storage_client=storage_client,
        youtube_downloader=InMemoryYoutubeDownloader(),
        ffmpeg_client=ffmpeg_client,
        stt_adapter=build_stt_adapter(),
        embedding_client=build_embedding_client(),
        vision_adapter=MockVisionAdapter(caption="caption"),
        workdir_manager=WorkdirManager(base_dir=Path.cwd()),
        chunking_service=ChunkingService(max_tokens=6, overlap_sentences=1),
        embedding_batch_size=2,
        stt_model_version="chirp_2",
        embedding_model_version="v001",
        max_source_size_bytes=1,
    )
    use_case = ProcessVideoUseCase(
        video_repository=video_repository,
        orchestrator=orchestrator,
        delete_video_use_case=DeleteVideoUseCase(
            video_repository=video_repository,
            artifact_repository=artifact_repository,
            storage_client=storage_client,
        ),
        stt_model_version="chirp_2",
        embedding_model_version="v001",
    )

    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")
    try:
        result = await use_case.execute(video_id=video_id, trace_id=str(trace_id))
    finally:
        logger.remove(sink_id)

    assert result.action == "failed"
    assert result.failed_stage == "EXTRACT"
    failed_video = await video_repository.get_video(video_id)
    assert failed_video.failed_stage == "EXTRACT"
    assert failed_video.failure_code == "SOURCE_LIMIT_EXCEEDED"
    assert failed_video.failure_trace_id == trace_id
    assert failed_video.processing_claimed_at is None
    assert any(
        "video.processing.failed failed_stage=EXTRACT "
        "failure_code=SOURCE_LIMIT_EXCEEDED" in message
        for message in messages
    )


@pytest.mark.asyncio
async def test_terminal_failure_hands_off_when_delete_wins_race(
    video_repository,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    storage_client.objects["videos/delete-race.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/delete-race.mp4",
            status="UPLOADED",
        )
    )

    async def delete_then_fail(**kwargs) -> None:
        del kwargs
        await video_repository.set_status(video_id, "DELETING")
        raise AudioPreparationError("Audio part upload failed: part-001.flac")

    orchestrator = AsyncMock()
    orchestrator.run.side_effect = delete_then_fail
    use_case = ProcessVideoUseCase(
        video_repository=video_repository,
        orchestrator=orchestrator,
        delete_video_use_case=DeleteVideoUseCase(
            video_repository=video_repository,
            artifact_repository=artifact_repository,
            storage_client=storage_client,
        ),
        stt_model_version="chirp_3",
        embedding_model_version="v001",
    )

    result = await use_case.execute(video_id=video_id, trace_id=str(trace_id))

    assert result.action == "deleted"
    assert await video_repository.get_video(video_id) is None
    assert "videos/delete-race.mp4" not in storage_client.objects


@pytest.mark.asyncio
async def test_process_video_maps_index_write_failure(
    video_repository,
    process_video_use_case,
    artifact_repository,
    storage_client,
    monkeypatch,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/source.mp4",
            status="UPLOADED",
        )
    )
    monkeypatch.setattr(
        artifact_repository,
        "persist_chunks_and_vectors",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id=str(trace_id),
    )

    failed_video = await video_repository.get_video(video_id)
    assert result.action == "failed"
    assert result.failed_stage == "VECTOR_UPSERT"
    assert failed_video.failure_code == "INDEX_WRITE_FAILED"
    assert failed_video.failure_trace_id == trace_id


@pytest.mark.asyncio
async def test_process_video_maps_unknown_chunking_failure_and_logs_once(
    video_repository,
    process_video_use_case,
    pipeline_orchestrator,
    storage_client,
    monkeypatch,
) -> None:
    video_id = str(uuid4())
    trace_id = uuid4()
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/source.mp4",
            status="UPLOADED",
        )
    )

    def fail_chunking(_segments) -> None:
        raise RuntimeError("unexpected chunk failure")

    monkeypatch.setattr(
        pipeline_orchestrator._chunking_service,
        "chunk_segments",
        fail_chunking,
    )
    failure_records = []
    sink_id = logger.add(
        lambda message: failure_records.append(message.record),
        filter=lambda record: record["message"].startswith("video.processing.failed"),
    )
    try:
        result = await process_video_use_case.execute(
            video_id=video_id,
            trace_id=str(trace_id),
        )
    finally:
        logger.remove(sink_id)

    failed_video = await video_repository.get_video(video_id)
    assert result.action == "failed"
    assert result.failed_stage == "CHUNKING"
    assert failed_video.failure_code == "INTERNAL_PROCESSING_ERROR"
    assert failed_video.failure_trace_id == trace_id
    assert len(failure_records) == 1
    assert failure_records[0]["extra"] == {
        "video_id": video_id,
        "trace_id": str(trace_id),
        "failed_stage": "CHUNKING",
        "failure_code": "INTERNAL_PROCESSING_ERROR",
    }
    assert failure_records[0]["exception"] is not None
    assert str(failure_records[0]["exception"].value) == "unexpected chunk failure"
