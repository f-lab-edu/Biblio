import asyncio
from pathlib import Path
from uuid import uuid4

import pytest

from src.infra.ai.google_stt_adapter import (
    ExternalAIAdapterError,
    GoogleSTTAdapter,
)
from src.infra.ai.vision_adapter import MockVisionAdapter
from src.infra.db.video_repository import VideoRecord
from src.infra.media.youtube_downloader import InMemoryYoutubeDownloader
from src.services.chunking_service import ChunkingService
from src.services.long_audio_transcription import (
    STT_INPUT_AUDIO_PART,
    LongAudioTranscriptionService,
)
from src.services.transcript_merge_service import TranscriptMergeService
from src.services.pipeline_orchestrator import PipelineOrchestrator
from src.usecases.delete_video import DeleteVideoUseCase
from src.usecases.process_video import ProcessVideoUseCase
from src.utils.workdir import WorkdirManager
from tests.support import build_embedding_client, build_ffmpeg_adapter


class PartWritingFFmpeg:
    def extract_audio_part(
        self,
        input_file: Path,
        output_file: Path,
        *,
        start_ms: int,
        end_ms: int,
        timeout: float,
    ) -> None:
        del input_file, timeout
        output_file.write_text(f"{start_ms}:{end_ms}")


def _build_service(
    *,
    artifact_repository,
    video_repository,
    storage_client,
    stt_adapter: GoogleSTTAdapter,
) -> LongAudioTranscriptionService:
    return LongAudioTranscriptionService(
        artifact_repository=artifact_repository,
        video_repository=video_repository,
        storage_client=storage_client,
        ffmpeg_client=PartWritingFFmpeg(),  # type: ignore[arg-type]
        stt_adapter=stt_adapter,
        merge_service=TranscriptMergeService(),
        part_duration_sec=900,
        part_overlap_sec=5,
        stt_concurrency=2,
        processing_timeout_sec=120,
    )


def test_plan_parts_covers_one_hour_with_five_second_overlaps() -> None:
    async def unused_client(audio_uri: str, trace_id: str) -> dict:
        del audio_uri, trace_id
        return {"segments": [], "stt_model_version": "chirp_3"}

    service = _build_service(
        artifact_repository=None,
        video_repository=None,
        storage_client=None,
        stt_adapter=GoogleSTTAdapter(client=unused_client, max_retries=0),
    )

    parts = service.plan_parts("video-1", 3_600_000)

    assert [(part.start_ms, part.end_ms) for part in parts] == [
        (0, 900_000),
        (895_000, 1_800_000),
        (1_795_000, 2_700_000),
        (2_695_000, 3_600_000),
    ]
    assert len({part.storage_path for part in parts}) == 4


@pytest.mark.asyncio
async def test_long_audio_success_merges_and_removes_temporary_assets(
    artifact_repository,
    video_repository,
    storage_client,
    tmp_path,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), status="PROCESSING")
    )
    source_path = tmp_path / "audio.flac"
    source_path.write_bytes(b"audio")

    async def stt_client(audio_uri: str, trace_id: str) -> dict:
        del trace_id
        is_second = "part-001" in audio_uri
        return {
            "segments": [],
            "words": [
                {
                    "text": "second." if is_second else "first.",
                    "start_ms": 10_000,
                    "end_ms": 10_500,
                }
            ],
            "stt_model_version": "chirp_3",
        }

    service = _build_service(
        artifact_repository=artifact_repository,
        video_repository=video_repository,
        storage_client=storage_client,
        stt_adapter=GoogleSTTAdapter(client=stt_client, max_retries=0),
    )

    result = await service.transcribe(
        video_id=video_id,
        audio_storage_path=f"artifacts/{video_id}/audio.flac",
        local_audio_path=source_path,
        duration_ms=1_200_001,
        workdir=tmp_path,
        trace_id="trace-long-success",
    )

    assert [segment.text for segment in result.segments] == ["first.", "second."]
    assert result.segments[1].start_ms == 905_000
    assert await artifact_repository.list_assets(
        video_id,
        asset_type=STT_INPUT_AUDIO_PART,
    ) == []
    assert not any("/stt-input/" in path for path in storage_client.objects)


@pytest.mark.asyncio
async def test_long_audio_accepts_a_silent_part(
    artifact_repository,
    video_repository,
    storage_client,
    tmp_path,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), status="PROCESSING")
    )
    source_path = tmp_path / "audio.flac"
    source_path.write_bytes(b"audio")

    async def stt_client(audio_uri: str, trace_id: str) -> dict:
        del trace_id
        words = []
        if "part-001" in audio_uri:
            words = [{"text": "heard.", "start_ms": 10_000, "end_ms": 10_500}]
        return {
            "segments": [],
            "words": words,
            "stt_model_version": "chirp_3",
        }

    service = _build_service(
        artifact_repository=artifact_repository,
        video_repository=video_repository,
        storage_client=storage_client,
        stt_adapter=GoogleSTTAdapter(client=stt_client, max_retries=0),
    )

    result = await service.transcribe(
        video_id=video_id,
        audio_storage_path=f"artifacts/{video_id}/audio.flac",
        local_audio_path=source_path,
        duration_ms=1_200_001,
        workdir=tmp_path,
        trace_id="trace-long-silence",
    )

    assert [segment.text for segment in result.segments] == ["heard."]
    assert result.segments[0].start_ms == 905_000


@pytest.mark.asyncio
async def test_failed_parallel_part_waits_for_sibling_before_cleanup(
    artifact_repository,
    video_repository,
    storage_client,
    tmp_path,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), status="PROCESSING")
    )
    source_path = tmp_path / "audio.flac"
    source_path.write_bytes(b"audio")
    events: list[str] = []
    original_delete_objects = storage_client.delete_objects
    upload_seen = False

    async def recording_upload(source: Path, storage_path: str) -> None:
        nonlocal upload_seen
        upload_seen = True
        await type(storage_client).upload_object(storage_client, source, storage_path)

    async def recording_delete(storage_paths: list[str]) -> None:
        if upload_seen:
            events.append("cleanup")
        await original_delete_objects(storage_paths)

    storage_client.upload_object = recording_upload
    storage_client.delete_objects = recording_delete

    async def stt_client(audio_uri: str, trace_id: str) -> dict:
        del trace_id
        if "part-000" in audio_uri:
            raise ExternalAIAdapterError(
                code="INVALID_REQUEST",
                message="bad part",
                trace_id="trace-long-failure",
                provider="google-stt",
                retryable=False,
            )
        await asyncio.sleep(0.01)
        events.append("sibling_done")
        return {
            "segments": [],
            "words": [{"text": "done.", "start_ms": 0, "end_ms": 100}],
            "stt_model_version": "chirp_3",
        }

    service = _build_service(
        artifact_repository=artifact_repository,
        video_repository=video_repository,
        storage_client=storage_client,
        stt_adapter=GoogleSTTAdapter(client=stt_client, max_retries=0),
    )

    with pytest.raises(ExternalAIAdapterError, match="bad part"):
        await service.transcribe(
            video_id=video_id,
            audio_storage_path=f"artifacts/{video_id}/audio.flac",
            local_audio_path=source_path,
            duration_ms=1_200_001,
            workdir=tmp_path,
            trace_id="trace-long-failure",
        )

    assert events.index("sibling_done") < events.index("cleanup")
    assert await artifact_repository.list_assets(
        video_id,
        asset_type=STT_INPUT_AUDIO_PART,
    ) == []


@pytest.mark.asyncio
async def test_long_audio_process_flow_reaches_ready_with_global_timestamps(
    artifact_repository,
    video_repository,
    storage_client,
    tmp_path,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/long.mp4"] = b"long-video"
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=str(uuid4()),
            storage_path="videos/long.mp4",
            status="UPLOADED",
        )
    )
    ffmpeg_client, _ = build_ffmpeg_adapter(duration_sec=1_201.0)

    async def stt_client(audio_uri: str, trace_id: str) -> dict:
        del trace_id
        is_second = "part-001" in audio_uri
        return {
            "segments": [],
            "words": [
                {
                    "text": "second." if is_second else "first.",
                    "start_ms": 10_000,
                    "end_ms": 10_500,
                }
            ],
            "stt_model_version": "chirp_3",
        }

    stt_adapter = GoogleSTTAdapter(client=stt_client, max_retries=0)
    long_audio_service = LongAudioTranscriptionService(
        artifact_repository=artifact_repository,
        video_repository=video_repository,
        storage_client=storage_client,
        ffmpeg_client=ffmpeg_client,
        stt_adapter=stt_adapter,
        merge_service=TranscriptMergeService(),
        part_duration_sec=900,
        part_overlap_sec=5,
        stt_concurrency=2,
        processing_timeout_sec=120,
    )
    orchestrator = PipelineOrchestrator(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        storage_client=storage_client,
        youtube_downloader=InMemoryYoutubeDownloader(),
        ffmpeg_client=ffmpeg_client,
        stt_adapter=stt_adapter,
        embedding_client=build_embedding_client(),
        vision_adapter=MockVisionAdapter(caption="caption"),
        workdir_manager=WorkdirManager(base_dir=tmp_path),
        chunking_service=ChunkingService(max_tokens=6, overlap_sentences=1),
        long_audio_transcription_service=long_audio_service,
        embedding_batch_size=2,
        stt_model_version="chirp_3",
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
        stt_model_version="chirp_3",
        embedding_model_version="v001",
    )

    result = await use_case.execute(video_id=video_id, trace_id="trace-long-flow")

    transcripts = await artifact_repository.load_transcripts(
        video_id,
        stt_model_version="chirp_3",
    )
    assert result.action == "processed"
    assert (await video_repository.get_video(video_id)).status == "READY"
    assert [segment.start_ms for segment in transcripts] == [10_000, 905_000]
    assert await artifact_repository.list_assets(
        video_id,
        asset_type=STT_INPUT_AUDIO_PART,
    ) == []
