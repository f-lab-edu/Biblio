from __future__ import annotations

from uuid import uuid4

import pytest

from adapters.db.artifact_repository import AssetRecord, TranscriptSegmentRecord
from adapters.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_resume_flow_reuses_existing_transcript_and_audio(
    video_repository,
    artifact_repository,
    process_video_use_case,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    storage_client.objects["artifacts/audio.flac"] = b"audio"
    video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/source.mp4",
            status="FAILED",
            failed_stage="CHUNKING",
        )
    )
    artifact_repository.create_asset(video_id, AssetRecord(asset_type="AUDIO", storage_path="artifacts/audio.flac"))
    artifact_repository.replace_transcripts(
        video_id,
        stt_model_version="google-stt-v1",
        segments=[TranscriptSegmentRecord(segment_index=0, text="alpha beta", start_ms=0, end_ms=1000, stt_model_version="google-stt-v1")],
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-resume",
        stt_model_version="google-stt-v1",
        embedding_model_version="v001",
    )

    assert result.action == "processed"
    assert len(artifact_repository.list_chunks(video_id)) > 0
