from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.infra.db.video_repository import VideoRecord
from src.infra.media.youtube_downloader import InMemoryYoutubeDownloader
from src.infra.storage.inmemory_storage import InMemoryStorageClient
from src.usecases.start_pipeline import StartPipelineUseCase
from src.utils.workdir import WorkdirManager


class _Videos:
    def __init__(self, video: VideoRecord | None) -> None:
        self.video = video
        self.failure = None

    async def get_video(self, _video_id: str) -> VideoRecord | None:
        return self.video

    async def set_failed(
        self,
        video_id,
        *,
        failed_stage,
        failure_code,
        failure_trace_id,
    ) -> bool:
        self.failure = (video_id, failed_stage, failure_code, failure_trace_id)
        return True


class _Work:
    def __init__(self) -> None:
        self.run_id = uuid4()

    async def start_pipeline_run(self, _video_id, _pipeline_version):
        return SimpleNamespace(id=self.run_id)


class _Scheduler:
    def __init__(self) -> None:
        self.calls = []

    async def dispatch_ready_work(self, stage, capacity, *, trace_id):
        self.calls.append((stage, capacity, trace_id))
        return 1


class _DeleteVideo:
    async def execute(self, *, video_ids, trace_id):
        del video_ids, trace_id


def _build_use_case(*, video, storage, downloader, tmp_path):
    work = _Work()
    scheduler = _Scheduler()
    use_case = StartPipelineUseCase(
        video_repository=_Videos(video),
        work_repository=work,
        scheduler=scheduler,
        storage=storage,
        youtube_downloader=downloader,
        workdirs=WorkdirManager(base_dir=tmp_path),
        delete_video=_DeleteVideo(),
        pipeline_version="work-unit-v1",
        normalization_capacity=1,
    )
    return use_case, work, scheduler


@pytest.mark.asyncio
async def test_external_source_is_uploaded_before_normalization_dispatch(tmp_path) -> None:
    video_id = uuid4()
    trace_id = uuid4()
    video = VideoRecord(
        id=video_id,
        user_id=uuid4(),
        input_type="EXTERNAL_URL",
        source_url="https://youtube.test/watch?v=1",
        storage_path=f"videos/{video_id}/source.mp4",
        status="PENDING",
    )
    storage = InMemoryStorageClient()
    downloader = InMemoryYoutubeDownloader(default_content=b"source")
    use_case, work, scheduler = _build_use_case(
        video=video,
        storage=storage,
        downloader=downloader,
        tmp_path=tmp_path,
    )

    result = await use_case.execute(video_id=str(video_id), trace_id=str(trace_id))

    assert result.pipeline_run_id == work.run_id
    assert storage.objects[video.storage_path] == b"source"
    assert len(downloader.downloads) == 1
    assert scheduler.calls == [("NORMALIZE_VIDEO", 1, trace_id)]


@pytest.mark.asyncio
async def test_existing_external_source_is_reused(tmp_path) -> None:
    video_id = uuid4()
    video = VideoRecord(
        id=video_id,
        user_id=uuid4(),
        input_type="EXTERNAL_URL",
        source_url="https://youtube.test/watch?v=1",
        storage_path=f"videos/{video_id}/source.mp4",
        status="PROCESSING",
    )
    storage = InMemoryStorageClient({video.storage_path: b"existing"})
    downloader = InMemoryYoutubeDownloader()
    use_case, _, _ = _build_use_case(
        video=video,
        storage=storage,
        downloader=downloader,
        tmp_path=tmp_path,
    )

    await use_case.execute(video_id=str(video_id), trace_id=str(uuid4()))

    assert downloader.downloads == []


@pytest.mark.asyncio
async def test_external_source_failure_marks_video_failed(tmp_path) -> None:
    video_id = uuid4()
    trace_id = uuid4()
    video = VideoRecord(
        id=video_id,
        user_id=uuid4(),
        input_type="EXTERNAL_URL",
        source_url=None,
        storage_path=f"videos/{video_id}/source.mp4",
        status="PENDING",
    )
    videos = _Videos(video)
    use_case = StartPipelineUseCase(
        video_repository=videos,
        work_repository=_Work(),
        scheduler=_Scheduler(),
        storage=InMemoryStorageClient(),
        youtube_downloader=InMemoryYoutubeDownloader(),
        workdirs=WorkdirManager(base_dir=tmp_path),
        delete_video=_DeleteVideo(),
        pipeline_version="work-unit-v1",
        normalization_capacity=1,
    )

    result = await use_case.execute(video_id=str(video_id), trace_id=str(trace_id))

    assert result.action == "failed"
    assert videos.failure is not None
    assert videos.failure[1] == "DOWNLOAD"
