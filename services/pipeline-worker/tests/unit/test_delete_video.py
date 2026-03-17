from __future__ import annotations

from uuid import uuid4

import pytest

from adapters.db.artifact_repository import AssetRecord
from adapters.db.video_repository import VideoRecord


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

    result = await delete_video_use_case.execute(video_id=video_id, trace_id="trace-1")

    assert result.deleted is True
    assert await video_repository.get_video(video_id) is None


@pytest.mark.asyncio
async def test_delete_video_duplicate_is_safe(delete_video_use_case) -> None:
    result = await delete_video_use_case.execute(video_id=str(uuid4()), trace_id="trace-2")

    assert result.duplicate is True
