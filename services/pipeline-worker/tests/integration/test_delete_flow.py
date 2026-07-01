from uuid import uuid4

import pytest

from src.infra.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_delete_flow_removes_video_and_storage(
    video_repository,
    artifact_repository,
    delete_video_use_case,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="DELETING")
    )

    result = await delete_video_use_case.execute(video_ids=[video_id], trace_id="trace-delete")

    assert result.deleted_count == 1
    assert "videos/source.mp4" in storage_client.deleted_paths


@pytest.mark.asyncio
async def test_delete_flow_retries_after_storage_failure(
    video_repository,
    delete_video_use_case,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/retry.mp4"] = b"video"
    storage_client.fail_delete_objects_once_for.add("videos/retry.mp4")
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/retry.mp4", status="DELETING")
    )

    with pytest.raises(RuntimeError):
        await delete_video_use_case.execute(video_ids=[video_id], trace_id="trace-fail")

    assert await video_repository.get_video(video_id) is not None

    result = await delete_video_use_case.execute(video_ids=[video_id], trace_id="trace-retry")

    assert result.deleted_count == 1
    assert await video_repository.get_video(video_id) is None
    assert "videos/retry.mp4" in storage_client.deleted_paths
