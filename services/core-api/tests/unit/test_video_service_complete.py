from uuid import uuid4

import pytest

from src.infra.db.video_repository import VideoRepository
from src.infra.inmemory_broker import InMemoryBrokerClient
from src.infra.inmemory_storage import InMemoryStorageClient
from src.infra.storage import MAX_UPLOAD_SIZE_BYTES
from src.middlewares.error_handler import ApiError, ForbiddenError, InvalidArgumentError, NotFoundError
from src.schemas.video_dto import VideoCompleteRequest
from src.services.video_service import VideoService
from tests.support import SessionFactory, build_video, seed_video


@pytest.mark.asyncio
async def test_complete_video_transitions_pending_local_file_and_publishes(
    session_factory: SessionFactory,
) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    storage_client = InMemoryStorageClient()
    storage_client.put_object(video.storage_path, b"video-bytes", etag="blob-etag")
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    trace_id = uuid4()

    result = await service.complete_video(
        video.id,
        VideoCompleteRequest(etag="blob-etag", size_bytes=11),
        requester_user_id=requester_user_id,
        trace_id=trace_id,
    )

    assert result.status_code == 202
    assert result.payload.video_id == video.id
    assert result.payload.status == "UPLOADED"
    assert len(broker_client.published_messages) == 1
    assert broker_client.published_messages[0]["message_type"] == "PREPROCESS_REQUEST"
    assert broker_client.published_messages[0]["payload_version"] == "v1"
    assert broker_client.published_messages[0]["trace_id"] == str(trace_id)
    assert broker_client.published_messages[0]["attempt"] == 1
    assert broker_client.published_messages[0]["video_id"] == str(video.id)

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(video.id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "UPLOADED"


@pytest.mark.asyncio
async def test_complete_video_is_idempotent_for_uploaded_processing_or_ready(
    session_factory: SessionFactory,
) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id, status="PROCESSING")
    storage_client = InMemoryStorageClient()
    broker_client = InMemoryBrokerClient()
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=broker_client,
    )
    await seed_video(session_factory, video)

    result = await service.complete_video(
        video.id,
        VideoCompleteRequest(),
        requester_user_id=requester_user_id,
        trace_id=uuid4(),
    )

    assert result.status_code == 200
    assert result.payload.status == "PROCESSING"
    assert broker_client.published_messages == []


@pytest.mark.asyncio
async def test_complete_video_rejects_missing_blob(session_factory: SessionFactory) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(InvalidArgumentError) as exc_info:
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert "not found in storage" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_complete_video_rejects_object_larger_than_2gb(session_factory: SessionFactory) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    storage_client = InMemoryStorageClient()
    storage_client.put_object(video.storage_path, b"x" * 8)
    storage_client.get_blob_metadata(video.storage_path)
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=type(
            "LargeObjectStorage",
            (),
            {
                "generate_signed_url": staticmethod(lambda request: None),
                "delete_object": staticmethod(lambda object_name: True),
                "get_blob_metadata": staticmethod(
                    lambda object_name: type(
                        "BlobMetadataLike",
                        (),
                        {"exists": True, "size_bytes": MAX_UPLOAD_SIZE_BYTES + 1, "etag": "etag"},
                    )()
                ),
            },
        )(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(InvalidArgumentError) as exc_info:
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert "2gb" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_complete_video_returns_403_for_other_user(session_factory: SessionFactory) -> None:
    owner_id = uuid4()
    other_user_id = uuid4()
    video = build_video(user_id=owner_id)
    await seed_video(session_factory, video)
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(ForbiddenError):
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=other_user_id,
            trace_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_complete_video_returns_404_for_unknown_video(session_factory: SessionFactory) -> None:
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=InMemoryStorageClient(),
        broker_client=InMemoryBrokerClient(),
    )

    with pytest.raises(NotFoundError):
        await service.complete_video(
            uuid4(),
            VideoCompleteRequest(),
            requester_user_id=uuid4(),
            trace_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_complete_video_keeps_uploaded_status_when_broker_publish_fails(
    session_factory: SessionFactory,
) -> None:
    requester_user_id = uuid4()
    video = build_video(user_id=requester_user_id)
    await seed_video(session_factory, video)
    storage_client = InMemoryStorageClient()
    storage_client.put_object(video.storage_path, b"video-bytes")
    service = VideoService(
        db_session_factory=session_factory,
        storage_client=storage_client,
        broker_client=InMemoryBrokerClient(failures_before_success=3),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.complete_video(
            video.id,
            VideoCompleteRequest(),
            requester_user_id=requester_user_id,
            trace_id=uuid4(),
        )

    assert "publish failed after retries" in str(exc_info.value).lower()

    async with session_factory() as session:
        repository = VideoRepository(session)
        stored_video = await repository.get_by_id_for_user(video.id, requester_user_id)

    assert stored_video is not None
    assert stored_video.status == "UPLOADED"
