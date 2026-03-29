from dataclasses import dataclass

from src.infra.db.artifact_repository import ArtifactRepository
from src.infra.db.video_repository import VideoRepository
from src.infra.storage.client import StorageClient


@dataclass(slots=True)
class DeleteVideoResult:
    deleted: bool
    duplicate: bool


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

    async def execute(self, *, video_id: str, trace_id: str) -> DeleteVideoResult:
        video = await self._video_repository.get_video(video_id)
        if video is None:
            return DeleteVideoResult(deleted=False, duplicate=True)

        storage_paths = await self._artifact_repository.delete_video_artifacts(video_id)
        if video.storage_path:
            storage_paths.append(video.storage_path)
        await self._video_repository.hard_delete_video(video_id)
        for storage_path in storage_paths:
            await self._storage_client.delete_object(storage_path)
        return DeleteVideoResult(deleted=True, duplicate=False)
