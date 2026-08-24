from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from src.infra.db.pipeline_work_repository import (
    PipelineVideoDeletingError,
    PipelineVideoNotFoundError,
    PipelineWorkRepository,
)
from src.infra.db.video_repository import VideoRecord, VideoRepository
from src.infra.media.youtube_downloader import DownloadError, YoutubeDownloader
from src.infra.storage.client import StorageClient
from src.services.failure_classifier import classify_pipeline_failure
from src.services.pipeline_work_scheduler import PipelineWorkScheduler
from src.utils.workdir import WorkdirManager


class DeleteVideo(Protocol):
    async def execute(self, *, video_ids: list[str], trace_id: str): ...


@dataclass(frozen=True, slots=True)
class StartPipelineResult:
    action: str
    pipeline_run_id: UUID | None = None


class StartPipelineUseCase:
    def __init__(
        self,
        *,
        video_repository: VideoRepository,
        work_repository: PipelineWorkRepository,
        scheduler: PipelineWorkScheduler,
        storage: StorageClient,
        youtube_downloader: YoutubeDownloader,
        workdirs: WorkdirManager,
        delete_video: DeleteVideo,
        pipeline_version: str,
        normalization_capacity: int,
    ) -> None:
        self._videos = video_repository
        self._work = work_repository
        self._scheduler = scheduler
        self._storage = storage
        self._youtube = youtube_downloader
        self._workdirs = workdirs
        self._delete_video = delete_video
        self._pipeline_version = pipeline_version
        self._normalization_capacity = normalization_capacity

    async def execute(self, *, video_id: str, trace_id: str) -> StartPipelineResult:
        video = await self._videos.get_video(video_id)
        if video is None:
            return StartPipelineResult(action="skipped")
        if video.status == "DELETING":
            await self._delete_video.execute(video_ids=[video_id], trace_id=trace_id)
            return StartPipelineResult(action="deleting")

        try:
            uploaded_by_this_call = await self._materialize_external_source(video)
        except Exception as error:
            failure = classify_pipeline_failure(error)
            marked_failed = await self._videos.set_failed(
                video_id,
                failed_stage=failure.failed_stage,
                failure_code=failure.failure_code,
                failure_trace_id=trace_id,
            )
            if marked_failed:
                return StartPipelineResult(action="failed")
            await self._delete_video.execute(video_ids=[video_id], trace_id=trace_id)
            return StartPipelineResult(action="deleting")
        try:
            run = await self._work.start_pipeline_run(video.id, self._pipeline_version)
        except PipelineVideoNotFoundError:
            return StartPipelineResult(action="skipped")
        except PipelineVideoDeletingError:
            if uploaded_by_this_call and video.storage_path:
                await self._storage.delete_object(video.storage_path)
            await self._delete_video.execute(video_ids=[video_id], trace_id=trace_id)
            return StartPipelineResult(action="deleting")

        if run is None:
            return StartPipelineResult(action="skipped")
        await self._scheduler.dispatch_ready_work(
            "NORMALIZE_VIDEO",
            self._normalization_capacity,
            trace_id=UUID(trace_id),
        )
        return StartPipelineResult(action="started", pipeline_run_id=run.id)

    async def _materialize_external_source(self, video: VideoRecord) -> bool:
        if video.input_type != "EXTERNAL_URL":
            return False
        if not video.storage_path:
            raise FileNotFoundError(f"Video storage_path missing for {video.id}")
        if await self._storage.object_exists(video.storage_path):
            return False
        if not video.source_url:
            raise DownloadError(f"Video source_url missing for {video.id}")

        with self._workdirs.temporary(video.id) as workdir:
            source_path = Path(workdir) / "source.mp4"
            await self._youtube.download(video.source_url, source_path)
            return await self._storage.upload_object_if_absent(
                source_path,
                video.storage_path,
            )
