from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from loguru import logger
from sqlalchemy import update

from src.infra.db.models import VideoModel
from src.infra.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_process_flow_end_to_end(
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
        trace_id="trace-process",
    )

    chunks = await artifact_repository.list_chunks(video_id)
    assert result.action == "processed"
    assert chunks
    assert all(chunk.visual_caption == "caption" for chunk in chunks)
    # 오디오 아티팩트가 GCS에 저장되었는지 확인
    assert f"artifacts/{video_id}/audio.flac" in storage_client.objects


@pytest.mark.asyncio
async def test_stale_processing_message_is_reclaimed_and_reaches_ready(
    video_repository,
    process_video_use_case,
    storage_client,
    session_factory,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/source.mp4",
            status="UPLOADED",
        )
    )
    assert await video_repository.claim_processing(video_id) is True
    async with session_factory() as session:
        await session.execute(
            update(VideoModel)
            .where(VideoModel.id == UUID(video_id))
            .values(
                processing_claimed_at=(
                    datetime.now(timezone.utc) - timedelta(seconds=1501)
                )
            )
        )
        await session.commit()

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-stale-recovery",
    )

    video = await video_repository.get_video(video_id)
    assert result.action == "processed"
    assert video is not None
    assert video.status == "READY"


@pytest.mark.asyncio
async def test_process_flow_logs_step_timings(
    video_repository,
    process_video_use_case,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")
    try:
        result = await process_video_use_case.execute(
            video_id=video_id,
            trace_id="trace-process-timing",
        )
    finally:
        logger.remove(sink_id)

    assert result.action == "processed"
    timing_logs = [message for message in messages if "pipeline.timing" in message]
    assert timing_logs
    timing_log = timing_logs[-1]
    assert "status=success" in timing_log
    assert "download_ms=" in timing_log
    assert "audio_ms=" in timing_log
    assert "stt_ms=" in timing_log
    assert "chunk_enrichment_ms=" in timing_log
    assert "embedding_ms=" in timing_log
    assert "persist_ms=" in timing_log
    assert "total_ms=" in timing_log


@pytest.mark.asyncio
async def test_process_flow_downloads_external_url_to_storage_path_and_marks_ready(
    video_repository,
    artifact_repository,
    process_video_use_case,
    storage_client,
    youtube_downloader,
) -> None:
    video_id = str(uuid4())
    storage_path = "videos/external/original"
    youtube_downloader.objects["https://youtu.be/external"] = b"downloaded-mp4"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            input_type="EXTERNAL_URL",
            source_url="https://youtu.be/external",
            storage_path=storage_path,
            status="PENDING",
        )
    )
    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-external-url",
    )

    video = await video_repository.get_video(video_id)
    assert result.action == "processed"
    assert video.status == "READY"
    assert storage_client.objects[storage_path] == b"downloaded-mp4"
    assert storage_client.content_types[storage_path] == "video/mp4"
    assert await artifact_repository.list_chunks(video_id)
