from uuid import uuid4

import pytest
from sqlalchemy import select

from src.infra.db.models import PipelineRunModel, VideoModel
from src.infra.db.pipeline_work_repository import (
    PipelineVideoDeletingError,
    PipelineVideoNotFoundError,
    PipelineWorkRepository,
)


async def _create_video(session_factory, *, status: str = "PROCESSING"):
    video_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="pipeline work test",
                    category="test",
                    input_type="FILE",
                    status=status,
                )
            )
    return video_id


class TestCreatePipelineRun:
    @pytest.mark.asyncio
    async def test_creates_first_active_run(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)

        created = await repository.create_pipeline_run(video_id, "pipeline-v1")

        assert created.video_id == video_id
        assert created.pipeline_version == "pipeline-v1"
        assert created.status == "RUNNING"
        assert created.is_active is True
        assert created.normalization_status == "READY"
        assert created.normalization_timestamps.ready_at is not None
        assert created.created_at is not None

    @pytest.mark.asyncio
    async def test_supersedes_active_run_before_creating_new_run(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        previous = await repository.create_pipeline_run(video_id, "pipeline-v1")

        current = await repository.create_pipeline_run(video_id, "pipeline-v2")

        async with session_factory() as session:
            models = list(
                await session.scalars(
                    select(PipelineRunModel).where(
                        PipelineRunModel.video_id == video_id
                    )
                )
            )
        by_id = {model.id: model for model in models}
        assert by_id[previous.id].status == "SUPERSEDED"
        assert by_id[previous.id].is_active is False
        assert by_id[current.id].status == "RUNNING"
        assert by_id[current.id].is_active is True

    @pytest.mark.asyncio
    async def test_rejects_missing_video_without_creating_run(self, session_factory):
        repository = PipelineWorkRepository(session_factory)

        with pytest.raises(PipelineVideoNotFoundError):
            await repository.create_pipeline_run(uuid4(), "pipeline-v1")

        async with session_factory() as session:
            assert await session.scalar(select(PipelineRunModel.id)) is None

    @pytest.mark.asyncio
    async def test_rejects_deleting_video_without_creating_run(self, session_factory):
        video_id = await _create_video(session_factory, status="DELETING")
        repository = PipelineWorkRepository(session_factory)

        with pytest.raises(PipelineVideoDeletingError):
            await repository.create_pipeline_run(video_id, "pipeline-v1")

        async with session_factory() as session:
            assert await session.scalar(select(PipelineRunModel.id)) is None


class TestGetActivePipelineRun:
    @pytest.mark.asyncio
    async def test_returns_current_run_after_replacement(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)
        await repository.create_pipeline_run(video_id, "pipeline-v1")
        current = await repository.create_pipeline_run(video_id, "pipeline-v2")

        found = await repository.get_active_pipeline_run(video_id)

        assert found is not None
        assert found.id == current.id

    @pytest.mark.asyncio
    async def test_returns_none_when_video_has_no_run(self, session_factory):
        video_id = await _create_video(session_factory)
        repository = PipelineWorkRepository(session_factory)

        assert await repository.get_active_pipeline_run(video_id) is None
