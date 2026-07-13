from unittest.mock import AsyncMock

import pytest

from src.services.pipeline_orchestrator import PipelineOrchestrator


def _build_orchestrator(video_repository) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        video_repository=video_repository,
        artifact_repository=None,  # type: ignore[arg-type]
        storage_client=None,  # type: ignore[arg-type]
        youtube_downloader=None,  # type: ignore[arg-type]
        ffmpeg_client=None,  # type: ignore[arg-type]
        stt_adapter=None,  # type: ignore[arg-type]
        embedding_client=None,  # type: ignore[arg-type]
        vision_adapter=None,  # type: ignore[arg-type]
        workdir_manager=None,  # type: ignore[arg-type]
        chunking_service=None,  # type: ignore[arg-type]
        embedding_batch_size=16,
        stt_model_version="chirp_2",
        embedding_model_version="fake-v001",
    )


def test_pipeline_orchestrator_defaults_chunk_concurrency_to_two() -> None:
    orchestrator = _build_orchestrator(None)

    assert orchestrator._chunk_concurrency == 2


@pytest.mark.asyncio
async def test_stage_boundary_touches_processing_claim_before_delete_check() -> None:
    video_repository = AsyncMock()
    video_repository.is_deleting.return_value = False
    orchestrator = _build_orchestrator(video_repository)

    await orchestrator._assert_not_deleting("video-id")

    video_repository.touch_processing.assert_awaited_once_with("video-id")
    video_repository.is_deleting.assert_awaited_once_with("video-id")
