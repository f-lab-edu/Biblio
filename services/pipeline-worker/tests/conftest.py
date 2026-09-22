from pathlib import Path

import pytest
import pytest_asyncio

from src.infra.db.artifact_repository import ArtifactRepository
from src.infra.db.pipeline_work_repository import PipelineWorkRepository
from src.infra.db.video_repository import VideoRepository
from src.infra.media.youtube_downloader import InMemoryYoutubeDownloader
from src.infra.storage.inmemory_storage import InMemoryStorageClient
from src.usecases.delete_video import DeleteVideoUseCase

from tests.support import build_ffmpeg_adapter, create_test_engine, make_session_factory


class MimeRecordingStorageClient(InMemoryStorageClient):
    def __init__(self) -> None:
        super().__init__()
        self.content_types: dict[str, str | None] = {}

    async def upload_object(self, source: Path, storage_path: str) -> None:
        await super().upload_object(source, storage_path)
        self.content_types[storage_path] = "video/mp4" if source.suffix == ".mp4" else None


@pytest_asyncio.fixture
async def session_factory():
    engine = await create_test_engine()
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def video_repository(session_factory):
    return VideoRepository(session_factory, stale_processing_reclaim_sec=1500)


@pytest.fixture
def artifact_repository(session_factory):
    return ArtifactRepository(session_factory)


@pytest.fixture
def pipeline_work_repository(session_factory):
    return PipelineWorkRepository(session_factory)


@pytest.fixture
def storage_client():
    return MimeRecordingStorageClient()


@pytest.fixture
def youtube_downloader():
    return InMemoryYoutubeDownloader()


@pytest.fixture
def ffmpeg_bundle():
    return build_ffmpeg_adapter()


@pytest.fixture
def delete_video_use_case(
    video_repository,
    artifact_repository,
    pipeline_work_repository,
    storage_client,
):
    return DeleteVideoUseCase(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        pipeline_work_repository=pipeline_work_repository,
        storage_client=storage_client,
    )
