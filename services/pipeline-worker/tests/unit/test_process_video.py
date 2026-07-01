from pathlib import Path
from uuid import uuid4

import pytest

from src.infra.ai.vision_adapter import MockVisionAdapter
from src.infra.db.video_repository import VideoRecord
from src.infra.media.youtube_downloader import DownloadError, InMemoryYoutubeDownloader
from src.services.chunking_service import ChunkingService
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
async def test_process_video_fails_on_missing_storage_object(
    video_repository,
    process_video_use_case,
) -> None:
    video_id = str(uuid4())
    # storage_path DB에 있지만 storage_client에는 없음 → download에서 FileNotFoundError
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/missing.mp4", status="UPLOADED")
    )

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-fail-dl",
    )

    assert result.action == "failed"
    assert result.failed_stage == "DOWNLOAD"
    video = await video_repository.get_video(video_id)
    assert video.status == "FAILED"
    assert video.failed_stage == "DOWNLOAD"


@pytest.mark.asyncio
async def test_process_video_fails_on_embedding_exhausted(
    video_repository,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
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
        trace_id="trace-embed-fail",
    )

    assert result.action == "failed"
    assert result.failed_stage == "EMBEDDING"
    video = await video_repository.get_video(video_id)
    assert video.status == "FAILED"
    assert video.failed_stage == "EMBEDDING"


@pytest.mark.asyncio
async def test_process_video_fails_on_stt_exhausted(
    video_repository,
    artifact_repository,
    storage_client,
) -> None:
    video_id = str(uuid4())
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
        trace_id="trace-stt-fail",
    )

    assert result.action == "failed"
    assert result.failed_stage == "STT"
    video = await video_repository.get_video(video_id)
    assert video.status == "FAILED"
    assert video.failed_stage == "STT"


@pytest.mark.asyncio
async def test_process_video_claim_conflict_skips(
    video_repository,
    process_video_use_case,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="UPLOADED")
    )
    # 다른 워커가 먼저 claim → PROCESSING 상태. claim_processing은 PENDING/UPLOADED/FAILED만 허용하므로 0 rows 반환
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
    source_url = "https://youtu.be/private"
    youtube_downloader.errors[source_url] = DownloadError("video is private")
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

    result = await process_video_use_case.execute(
        video_id=video_id,
        trace_id="trace-download-error",
    )

    video = await video_repository.get_video(video_id)
    assert result.action == "failed"
    assert result.failed_stage == "DOWNLOAD"
    assert video.status == "FAILED"
    assert video.failed_stage == "DOWNLOAD"
