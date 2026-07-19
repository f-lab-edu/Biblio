from dataclasses import dataclass

from src.infra.db.artifact_repository import ArtifactRepository
from src.infra.db.video_repository import VideoRepository
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
        storage_client: StorageClient,
    ) -> None:
        self._video_repository = video_repository
        self._artifact_repository = artifact_repository
        self._storage_client = storage_client

    async def execute(self, *, video_ids: list[str], trace_id: str) -> DeleteVideoResult:
        del trace_id
        unique_video_ids = list(dict.fromkeys(video_ids))
        videos = await self._video_repository.get_videos(unique_video_ids)
        if not videos:
            return DeleteVideoResult(deleted_count=0, duplicate_count=len(unique_video_ids))

        found_video_ids = [video.id for video in videos]
        if await self._video_repository.has_fresh_processing_claim(found_video_ids):
            raise DeletionDeferred("Video processing cleanup is still active")
        artifact_paths = await self._artifact_repository.list_storage_paths(found_video_ids)
        storage_paths = self._storage_paths_for(videos, artifact_paths)

        if storage_paths:
            await self._storage_client.delete_objects(storage_paths)
        await self._artifact_repository.delete_videos_artifacts(found_video_ids)
        await self._video_repository.hard_delete_videos(found_video_ids)
        return DeleteVideoResult(
            deleted_count=len(videos),
            duplicate_count=len(unique_video_ids) - len(videos),
        )

    @staticmethod
    def _storage_paths_for(videos, artifact_paths) -> list[str]:
        storage_paths: list[str] = []
        for video in videos:
            storage_paths.extend(artifact_paths.get(video.id, []))
            if video.storage_path:
                storage_paths.append(video.storage_path)
        return list(dict.fromkeys(storage_paths))
