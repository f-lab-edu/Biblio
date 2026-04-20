from pathlib import Path
from uuid import uuid4

import pytest

from src.infra.ai.vision_adapter import MockVisionAdapter
from src.infra.db.video_repository import VideoRecord
from src.services.chunking_service import ChunkingService
from src.services.pipeline_orchestrator import PipelineOrchestrator
from tests.support import build_embedding_client, build_ffmpeg_adapter, build_stt_adapter
from src.usecases.delete_video import DeleteVideoUseCase
from src.usecases.process_video import ProcessVideoUseCase
from src.utils.workdir import WorkdirManager


@pytest.mark.asyncio
async def test_deleting_interrupt_hands_off_to_delete_flow(
    video_repository,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )
    await video_repository.set_status(video_id, "DELETING")

    ffmpeg_client, _runner = build_ffmpeg_adapter()
    orchestrator = PipelineOrchestrator(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        storage_client=storage_client,
        ffmpeg_client=ffmpeg_client,
        stt_adapter=build_stt_adapter(),
        embedding_client=build_embedding_client(),
        vision_adapter=MockVisionAdapter(caption="caption"),
        workdir_manager=WorkdirManager(base_dir=Path.cwd()),
        chunking_service=ChunkingService(max_tokens=6, overlap_sentences=1),
        embedding_batch_size=2,
        stt_model_version="google-stt-v1",
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
        stt_model_version="google-stt-v1",
        embedding_model_version="v001",
    )

    result = await use_case.execute(
        video_id=video_id,
        trace_id="trace-delete",
    )

    assert result.action == "deleted"
    assert await video_repository.get_video(video_id) is None
