from __future__ import annotations

from uuid import uuid4

import pytest

from adapters.db.video_repository import VideoRecord


@pytest.mark.asyncio
async def test_delete_flow_removes_video_and_storage(
    video_repository,
    artifact_repository,
    delete_video_use_case,
    storage_client,
) -> None:
    video_id = str(uuid4())
    storage_client.objects["videos/source.mp4"] = b"video"
    video_repository.create_video(
        VideoRecord(id=video_id, user_id=str(uuid4()), storage_path="videos/source.mp4", status="DELETING")
    )

    result = await delete_video_use_case.execute(video_id=video_id, trace_id="trace-delete")

    assert result.deleted is True
    assert "videos/source.mp4" in storage_client.deleted_paths
