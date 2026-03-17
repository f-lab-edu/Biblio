from uuid import uuid4

import pytest

from adapters.db.artifact_repository import AssetRecord, ChunkRecord, TranscriptSegmentRecord
from adapters.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_repositories_support_claim_outputs_and_delete(video_repository, artifact_repository) -> None:
    video_id = str(uuid4())
    video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )

    assert video_repository.claim_processing(video_id) is True
    artifact_repository.create_asset(video_id, AssetRecord(asset_type="AUDIO", storage_path="artifacts/audio.flac"))
    artifact_repository.replace_transcripts(
        video_id,
        stt_model_version="google-stt-v1",
        segments=[TranscriptSegmentRecord(segment_index=0, text="hello", start_ms=0, end_ms=1, stt_model_version="google-stt-v1")],
    )
    artifact_repository.persist_chunks_and_vectors(
        video_id,
        chunks=[
            ChunkRecord(
                chunk_index=0,
                text="hello",
                enriched_text="hello",
                start_ms=0,
                end_ms=1,
                chunking_version="v1",
                stt_model_version="google-stt-v1",
                embedding_model_version="v001",
            )
        ],
        embeddings=[[1.0, 2.0]],
        set_ready=True,
    )

    state = video_repository.load_pipeline_state(
        video_id,
        stt_model_version="google-stt-v1",
        embedding_model_version="v001",
    )
    assert state.has_current_outputs is True
    paths = artifact_repository.delete_video_artifacts(video_id)
    assert "artifacts/audio.flac" in paths
