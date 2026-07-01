from uuid import uuid4

import pytest

from src.infra.db.artifact_repository import AssetRecord
from src.infra.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_delete_video_hard_deletes_records(
    video_repository,
    artifact_repository,
    delete_video_use_case,
    storage_client,
) -> None:
    video_id = str(uuid4())
    await video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="DELETING")
    )
    await artifact_repository.create_asset(video_id, AssetRecord(asset_type="AUDIO", storage_path="artifacts/audio.flac"))

    result = await delete_video_use_case.execute(video_ids=[video_id], trace_id="trace-1")

    assert result.deleted_count == 1
    assert result.duplicate_count == 0
    assert await video_repository.get_video(video_id) is None


@pytest.mark.asyncio
async def test_delete_video_duplicate_is_safe(delete_video_use_case) -> None:
    result = await delete_video_use_case.execute(video_ids=[str(uuid4())], trace_id="trace-2")

    assert result.deleted_count == 0
    assert result.duplicate_count == 1


@pytest.mark.asyncio
async def test_delete_video_deletes_multiple_videos_with_one_path(
    video_repository,
    artifact_repository,
    delete_video_use_case,
    storage_client,
) -> None:
    user_id = str(uuid4())
    first_video_id = str(uuid4())
    second_video_id = str(uuid4())
    storage_client.objects["videos/first.mp4"] = b"first"
    storage_client.objects["videos/second.mp4"] = b"second"
    storage_client.objects["artifacts/first.flac"] = b"audio"
    await video_repository.create_video(
        VideoRecord(id=first_video_id, user_id=user_id, storage_path="videos/first.mp4", status="DELETING")
    )
    await video_repository.create_video(
        VideoRecord(id=second_video_id, user_id=user_id, storage_path="videos/second.mp4", status="DELETING")
    )
    await artifact_repository.create_asset(
        first_video_id,
        AssetRecord(asset_type="AUDIO", storage_path="artifacts/first.flac"),
    )

    result = await delete_video_use_case.execute(
        video_ids=[first_video_id, second_video_id],
        trace_id="trace-many",
    )

    assert result.deleted_count == 2
    assert result.duplicate_count == 0
    assert await video_repository.get_video(first_video_id) is None
    assert await video_repository.get_video(second_video_id) is None
    assert len(storage_client.deleted_batches) == 1
    assert set(storage_client.deleted_batches[0]) == {
        "artifacts/first.flac",
        "videos/first.mp4",
        "videos/second.mp4",
    }


@pytest.mark.asyncio
async def test_delete_video_retries_after_partial_storage_failure(
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

    retry_result = await delete_video_use_case.execute(video_ids=[video_id], trace_id="trace-retry")

    assert retry_result.deleted_count == 1
    assert retry_result.duplicate_count == 0
    assert "videos/retry.mp4" not in storage_client.objects
