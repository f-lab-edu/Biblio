from uuid import uuid4

import pytest

from adapters.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_process_flow_end_to_end(
    video_repository,
    process_video_use_case,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-process",
        stt_model_version="google-stt-v1",
        embedding_model_version="v001",
    )

    chunks = artifact_repository.list_chunks(video_id)
    assert result.action == "processed"
    assert chunks
    assert all(chunk.visual_caption == "caption" for chunk in chunks)
