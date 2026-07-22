from uuid import uuid4

import pytest

from src.infra.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_set_failed_stores_failure_metadata(video_repository) -> None:
    video_id = uuid4()
    failure_trace_id = uuid4()
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=uuid4(), status="UPLOADED")
    )

    stored = await video_repository.set_failed(
        video_id,
        failed_stage="STT",
        failure_code="STT_FAILED",
        failure_trace_id=failure_trace_id,
    )

    video = await video_repository.get_video(video_id)
    assert stored is True
    assert video is not None
    assert video.status == "FAILED"
    assert video.failed_stage == "STT"
    assert video.failure_code == "STT_FAILED"
    assert video.failure_trace_id == failure_trace_id


@pytest.mark.asyncio
async def test_claim_processing_clears_previous_failure(video_repository) -> None:
    video_id = uuid4()
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=uuid4(),
            status="PENDING",
            failed_stage="STT",
            failure_code="STT_FAILED",
            failure_trace_id=uuid4(),
        )
    )

    claimed = await video_repository.claim_processing(video_id)

    video = await video_repository.get_video(video_id)
    assert claimed is True
    assert video is not None
    assert video.status == "PROCESSING"
    assert video.failed_stage is None
    assert video.failure_code is None
    assert video.failure_trace_id is None


@pytest.mark.asyncio
async def test_claim_processing_rejects_failed_video(video_repository) -> None:
    video_id = uuid4()
    failure_trace_id = uuid4()
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=uuid4(),
            status="FAILED",
            failed_stage="STT",
            failure_code="STT_FAILED",
            failure_trace_id=failure_trace_id,
        )
    )

    claimed = await video_repository.claim_processing(video_id)

    video = await video_repository.get_video(video_id)
    assert claimed is False
    assert video is not None
    assert video.status == "FAILED"
    assert video.failed_stage == "STT"
    assert video.failure_code == "STT_FAILED"
    assert video.failure_trace_id == failure_trace_id


@pytest.mark.asyncio
async def test_set_failed_does_not_overwrite_deleting_video(video_repository) -> None:
    video_id = uuid4()
    previous_trace_id = uuid4()
    await video_repository.create_video(
        VideoRecord(
            id=video_id,
            user_id=uuid4(),
            status="DELETING",
            failed_stage="DOWNLOAD",
            failure_code="SOURCE_UNAVAILABLE",
            failure_trace_id=previous_trace_id,
        )
    )

    stored = await video_repository.set_failed(
        video_id,
        failed_stage="STT",
        failure_code="STT_FAILED",
        failure_trace_id=uuid4(),
    )

    video = await video_repository.get_video(video_id)
    assert stored is False
    assert video is not None
    assert video.status == "DELETING"
    assert video.failed_stage == "DOWNLOAD"
    assert video.failure_code == "SOURCE_UNAVAILABLE"
    assert video.failure_trace_id == previous_trace_id
