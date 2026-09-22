import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from src.infra.db.artifact_repository import ArtifactRepository
from src.infra.db.pipeline_work_repository import PipelineWorkRepository
from src.infra.db.video_repository import VideoRecord, VideoRepository
from src.infra.storage.client import StorageClient


@dataclass(slots=True)
class DeleteVideoResult:
    deleted_count: int
    duplicate_count: int


class DeletionDeferred(Exception):
    """Keep the delete message unacknowledged while processing cleanup finishes."""


class DeleteVideoUseCase:
    def __init__(
        self,
        *,
        video_repository: VideoRepository,
        artifact_repository: ArtifactRepository,
        pipeline_work_repository: PipelineWorkRepository,
        storage_client: StorageClient,
    ) -> None:
        self._video_repository = video_repository
        self._artifact_repository = artifact_repository
        self._pipeline_work_repository = pipeline_work_repository
        self._storage_client = storage_client

    async def execute(self, *, video_ids: list[str], trace_id: str) -> DeleteVideoResult:
        del trace_id
        unique_video_ids = list(dict.fromkeys(video_ids))
        videos = await self._video_repository.get_videos(unique_video_ids)
        if not videos:
            return DeleteVideoResult(deleted_count=0, duplicate_count=len(unique_video_ids))

        deletable_videos = [video for video in videos if video.status == "DELETING"]
        if not deletable_videos:
            return DeleteVideoResult(
                deleted_count=0,
                duplicate_count=len(unique_video_ids),
            )
        found_video_ids = [video.id for video in deletable_videos]
        for video_id in found_video_ids:
            await self._pipeline_work_repository.cancel_pending_work_for_deleting_video(
                video_id
            )
        if await self._video_repository.has_fresh_processing_claim(found_video_ids):
            raise DeletionDeferred("Video processing cleanup is still active")
        await self._wait_for_pipeline_work(found_video_ids)
        artifact_paths = await self._artifact_repository.list_storage_paths(found_video_ids)
        pipeline_paths = await asyncio.gather(
            *(
                self._storage_client.list_objects(
                    f"artifacts/{video_id}/pipeline-runs/"
                )
                for video_id in found_video_ids
            )
        )
        storage_paths = self._storage_paths_for(
            deletable_videos,
            artifact_paths,
            pipeline_paths,
        )

        if storage_paths:
            await self._storage_client.delete_objects(storage_paths)
        await self._artifact_repository.delete_videos_artifacts(found_video_ids)
        await self._video_repository.hard_delete_videos(found_video_ids)
        return DeleteVideoResult(
            deleted_count=len(deletable_videos),
            duplicate_count=len(unique_video_ids) - len(deletable_videos),
        )

    async def _wait_for_pipeline_work(
        self,
        video_ids: Sequence[UUID | str],
    ) -> None:
        running_counts = await asyncio.gather(
            *(
                self._pipeline_work_repository.count_running_work(video_id)
                for video_id in video_ids
            )
        )
        if any(running_counts):
            raise DeletionDeferred("Pipeline work is still running")
        for video_id in video_ids:
            await self._pipeline_work_repository.finalize_deleting_runs(video_id)

    @staticmethod
    def _storage_paths_for(
        videos: Sequence[VideoRecord],
        artifact_paths: Mapping[UUID | str, Sequence[str]],
        pipeline_paths: Sequence[Sequence[str]],
    ) -> list[str]:
        storage_paths: list[str] = []
        for video, run_paths in zip(videos, pipeline_paths, strict=True):
            storage_paths.extend(artifact_paths.get(video.id, []))
            storage_paths.extend(run_paths)
            if video.storage_path:
                storage_paths.append(video.storage_path)
        return list(dict.fromkeys(storage_paths))
