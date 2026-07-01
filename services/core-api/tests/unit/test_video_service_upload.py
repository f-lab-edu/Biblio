from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.infra.db.video_repository import VideoRepository
from src.infra.inmemory_broker import InMemoryBrokerClient
from src.infra.inmemory_storage import InMemoryStorageClient
from src.infra.storage import MAX_UPLOAD_SIZE_BYTES
from src.middlewares.error_handler import ApiError, ConflictError
from src.models.admin_ops import Project
from src.schemas.video_dto import ExternalUrlVideoCreateRequest, LocalFileVideoCreateRequest
from src.services.video_service import VideoService
from tests.support import SessionFactory


@pytest.mark.asyncio
async def test_create_video_local_file_returns_signed_url_and_persists_pending_video(
    session_factory: SessionFactory,
) -> None:
    now = datetime(2026, 3, 12, 12, 0, tzinfo=UTC)
    storage_client = InMemoryStorageClient(now_provider=lambda: now)
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    requester_user_id = uuid4()

    result = await service.create_video(
        LocalFileVideoCreateRequest(
            title="Local video",
            category="GENERAL",
            input_type="LOCAL_FILE",
            extension=".mp4",
        ),
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )

    assert result.status_code == 201
    assert result.payload.status == "PENDING"
    assert result.payload.signed_url.endswith(".mp4?method=put")
    assert storage_client.generated_requests[0].max_size_bytes == MAX_UPLOAD_SIZE_BYTES

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(result.payload.video_id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "PENDING"
    assert stored_video.storage_path == f"videos/{requester_user_id}/{result.payload.video_id}/original.mp4"
    assert broker_client.published_messages == []


@pytest.mark.asyncio
async def test_create_video_external_url_publishes_preprocess_request(
    session_factory: SessionFactory,
) -> None:
    storage_client = InMemoryStorageClient()
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    requester_user_id = uuid4()
    trace_id = uuid4()

    result = await service.create_video(
        ExternalUrlVideoCreateRequest(
            title="External video",
            category="IT",
            input_type="EXTERNAL_URL",
            source_url="https://example.com/watch?v=1",
        ),
        requester_user_id=requester_user_id,
        trace_id=trace_id,
    )

    assert result.status_code == 202
    assert result.payload.status == "PENDING"
    assert broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"
    assert broker_client.published_messages[0]["trace_id"] == str(trace_id)
    assert broker_client.published_messages[0]["video_ids"] == [str(result.payload.video_id)]

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(result.payload.video_id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "PENDING"
    assert stored_video.source_url == "https://example.com/watch?v=1"
    assert stored_video.storage_path == f"videos/{requester_user_id}/{result.payload.video_id}/original"


@pytest.mark.asyncio
async def test_create_video_external_url_raises_500_after_broker_retries(
    session_factory: SessionFactory,
) -> None:
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(failures_before_success=3),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.create_video(
            ExternalUrlVideoCreateRequest(
                title="Broken external video",
                category="LEGAL",
                input_type="EXTERNAL_URL",
                source_url="https://example.com/broken",
            ),
            requester_user_id=uuid4(),
            trace_id=uuid4(),
        )

    assert "publish failed after retries" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_create_video_rejects_rollback_excluded_project(
    session_factory: SessionFactory,
) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    async with session_factory() as session:
        session.add(
            Project(
                id=project_id,
                user_id=requester_user_id,
                title="Recovering project",
                search_serving_state="ROLLBACK_EXCLUDED",
            )
        )
        await session.commit()

    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(ConflictError, match="rollback recovery"):
        await service.create_video(
            LocalFileVideoCreateRequest(
                title="Blocked upload",
                category="GENERAL",
                input_type="LOCAL_FILE",
                extension=".mp4",
            ),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
            project_id=project_id,
        )


@pytest.mark.asyncio
async def test_project_ingest_admission_rejects_rollback_excluded_project(monkeypatch) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()

    class _FakeAdminRepository:
        def __init__(self, session) -> None:
            self.session = session

        async def get_project(self, requested_project_id):
            # Async to satisfy the AdminRepository test double contract.
            return SimpleNamespace(
                id=requested_project_id,
                user_id=requester_user_id,
                search_serving_state="ROLLBACK_EXCLUDED",
            )

    monkeypatch.setattr("src.services.video_service.AdminRepository", _FakeAdminRepository)

    with pytest.raises(ConflictError, match="rollback recovery"):
        await VideoService._ensure_project_accepts_ingest(
            object(),
            project_id=project_id,
            requester_user_id=requester_user_id,
        )
