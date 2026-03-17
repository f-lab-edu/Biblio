from uuid import uuid4

import pytest

from adapters.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_process_video_happy_path(
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
        trace_id="trace-1",
        stt_model_version="google-stt-v1",
        embedding_model_version="v001",
    )

    assert result.action == "processed"
    assert len(artifact_repository.list_chunks(video_id)) > 0
    assert video_repository.get_video(video_id).status == "READY"


@pytest.mark.asyncio
async def test_process_video_skips_ready_same_version(
    video_repository,
    process_video_use_case,
    artifact_repository,
) -> None:
    video_id = str(uuid4())
    video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="READY")
    )
    artifact_repository.persist_chunks_and_vectors(
        video_id,
        chunks=[
            __import__("adapters.db.artifact_repository", fromlist=["ChunkRecord"]).ChunkRecord(
                chunk_index=0,
                text="text",
                enriched_text="text",
                start_ms=0,
                end_ms=1,
                chunking_version="v1",
                stt_model_version="google-stt-v1",
                embedding_model_version="v001",
            )
        ],
        embeddings=[[1.0]],
        set_ready=True,
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-2",
        stt_model_version="google-stt-v1",
        embedding_model_version="v001",
    )

    assert result.action == "skip"
