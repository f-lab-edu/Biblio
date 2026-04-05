from uuid import uuid4

import pytest
from loguru import logger

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
