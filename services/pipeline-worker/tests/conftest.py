import sys
from pathlib import Path

import pytest
import pytest_asyncio

SRC_PATH = Path(__file__).resolve().parents[1] / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from adapters.ai.vision_adapter import MockVisionAdapter
from adapters.db.artifact_repository import ArtifactRepository
from adapters.db.video_repository import VideoRepository
from adapters.storage.inmemory_storage import InMemoryStorageClient
from services.chunking_service import ChunkingService
from services.pipeline_orchestrator import PipelineOrchestrator
from usecases.delete_video import DeleteVideoUseCase
from usecases.process_video import ProcessVideoUseCase
from utils.workdir import WorkdirManager

from tests.support import build_embedding_client, build_ffmpeg_adapter, create_test_engine, make_session_factory, build_stt_adapter


@pytest_asyncio.fixture
async def session_factory():
    engine = await create_test_engine()
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def video_repository(session_factory):
    return VideoRepository(session_factory)


@pytest.fixture
def artifact_repository(session_factory):
    return ArtifactRepository(session_factory)


@pytest.fixture
def storage_client():
    return InMemoryStorageClient()


@pytest.fixture
def chunking_service():
    return ChunkingService(max_tokens=6, overlap_sentences=1)


@pytest.fixture
def ffmpeg_bundle():
    return build_ffmpeg_adapter()


@pytest.fixture
def pipeline_orchestrator(video_repository, artifact_repository, storage_client, chunking_service, ffmpeg_bundle):
    ffmpeg_client, _runner = ffmpeg_bundle
    return PipelineOrchestrator(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        storage_client=storage_client,
        ffmpeg_client=ffmpeg_client,
        stt_adapter=build_stt_adapter(),
        embedding_client=build_embedding_client(),
        vision_adapter=MockVisionAdapter(caption="caption", ocr_text="ocr", scene_tags="scene"),
        workdir_manager=WorkdirManager(base_dir=Path.cwd()),
        chunking_service=chunking_service,
        embedding_batch_size=2,
        stt_model_version="chirp_2",
        embedding_model_version="v001",
    )


@pytest.fixture
def delete_video_use_case(video_repository, artifact_repository, storage_client):
    return DeleteVideoUseCase(
        video_repository=video_repository,
        artifact_repository=artifact_repository,
        storage_client=storage_client,
    )


@pytest.fixture
def process_video_use_case(video_repository, pipeline_orchestrator, delete_video_use_case):
    return ProcessVideoUseCase(
        video_repository=video_repository,
        orchestrator=pipeline_orchestrator,
        delete_video_use_case=delete_video_use_case,
        stt_model_version="chirp_2",
        embedding_model_version="v001",
    )
