from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineFrameCandidateModel,
    PipelineRunModel,
    VideoModel,
)
from src.infra.db.normalization_repository import NormalizationRepository
from src.infra.db.pipeline_dispatch_unit_of_work import (
    SqlAlchemyPipelineDispatchUnitOfWork,
)
from src.infra.storage.inmemory_storage import InMemoryStorageClient
from src.schemas.messages import MessageType, NormalizeVideoMessage
from src.services.normalization_service import NormalizationService
from src.services.pipeline_work_scheduler import PipelineWorkScheduler
from src.utils.workdir import WorkdirManager


class _LocalMedia:
    def __init__(self, *, fail_once_at_start_ms: int | None = None) -> None:
        self.fail_once_at_start_ms = fail_once_at_start_ms
        self.part_ranges: list[tuple[int, int]] = []

    def probe_duration_ms(self, input_file: Path | str) -> int:
        assert str(input_file).startswith("https://storage.test/")
        return 1_020_000

    def extract_audio_part(
        self,
        input_file: Path | str,
        output_file: Path,
        *,
        start_ms: int,
        end_ms: int,
    ) -> None:
        assert str(input_file).startswith("https://storage.test/")
        self.part_ranges.append((start_ms, end_ms))
        if self.fail_once_at_start_ms == start_ms:
            self.fail_once_at_start_ms = None
            raise RuntimeError("interrupted audio extraction")
        output_file.write_bytes(f"audio:{start_ms}:{end_ms}".encode())

    def extract_frame_candidate(
        self,
        input_file: Path | str,
        output_file: Path,
        *,
        timestamp_ms: int,
        max_width: int,
    ) -> None:
        assert str(input_file).startswith("https://storage.test/")
        output_file.write_bytes(f"frame:{timestamp_ms}:{max_width}".encode())


class _Publisher:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, session, queue_name, payload) -> int:
        del session
        assert queue_name == "TRANSCRIBE_PART"
        self.messages.append(payload)
        return len(self.messages)


@pytest.mark.asyncio
async def test_normalization_persists_artifacts_and_dispatches_stt(
    session_factory,
    tmp_path: Path,
) -> None:
    video_id = uuid4()
    run_id = uuid4()
    source_path = "videos/source.mp4"
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="normalization integration",
                    category="test",
                    input_type="FILE",
                    storage_path=source_path,
                    status="PROCESSING",
                )
            )
            session.add(
                PipelineRunModel(
                    id=run_id,
                    video_id=video_id,
                    pipeline_version="pipeline-v1",
                    normalization_status="RUNNING",
                    normalization_started_at=datetime.now(UTC),
                )
            )

    publisher = _Publisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )
    repository = NormalizationRepository(
        session_factory=session_factory,
        publisher=publisher,
        scheduler=scheduler,
        stt_capacity=2,
    )
    storage = InMemoryStorageClient({source_path: b"video"})
    service = NormalizationService(
        media=_LocalMedia(),
        storage=storage,
        repository=repository,
        workdirs=WorkdirManager(tmp_path),
        part_duration_ms=900_000,
        overlap_ms=5_000,
        frame_interval_ms=600_000,
        frame_max_width=1280,
        frame_extraction_concurrency=2,
        stt_model_version="chirp-v3",
        signed_url_ttl_sec=8100,
    )
    message = NormalizeVideoMessage(
        message_type=MessageType.NORMALIZE_VIDEO,
        payload_version="v1",
        trace_id=uuid4(),
        attempt=1,
        pipeline_run_id=run_id,
        video_id=video_id,
        pipeline_version="pipeline-v1",
        issued_at=datetime.now(UTC),
    )

    await service.execute(message)

    async with session_factory() as session:
        run = await session.get(PipelineRunModel, run_id)
        parts = list(
            await session.scalars(
                select(PipelineAudioPartModel).order_by(
                    PipelineAudioPartModel.part_index
                )
            )
        )
        frames = list(
            await session.scalars(
                select(PipelineFrameCandidateModel).order_by(
                    PipelineFrameCandidateModel.frame_index
                )
            )
        )

    assert run is not None
    assert run.normalization_status == "COMPLETED"
    assert run.total_part_count == 2
    assert [(part.start_ms, part.end_ms) for part in parts] == [
        (0, 900_000),
        (895_000, 1_020_000),
    ]
    assert [part.status for part in parts] == ["DISPATCHED", "DISPATCHED"]
    assert [frame.timestamp_ms for frame in frames] == [300_000, 900_000]
    assert len(publisher.messages) == 2
    assert len(storage.objects) == 5
    assert list((tmp_path / "pipeline_worker_workdirs").iterdir()) == []


@pytest.mark.asyncio
async def test_retry_reuses_persisted_part_without_duplicate_dispatch(
    session_factory,
    tmp_path: Path,
) -> None:
    video_id = uuid4()
    run_id = uuid4()
    source_path = "videos/retry-source.mp4"
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="normalization retry",
                    category="test",
                    input_type="FILE",
                    storage_path=source_path,
                    status="PROCESSING",
                )
            )
            session.add(
                PipelineRunModel(
                    id=run_id,
                    video_id=video_id,
                    pipeline_version="pipeline-v1",
                    normalization_status="RUNNING",
                    normalization_started_at=datetime.now(UTC),
                )
            )

    publisher = _Publisher()
    scheduler = PipelineWorkScheduler(
        SqlAlchemyPipelineDispatchUnitOfWork(session_factory, publisher)
    )
    repository = NormalizationRepository(
        session_factory=session_factory,
        publisher=publisher,
        scheduler=scheduler,
        stt_capacity=2,
    )
    media = _LocalMedia(fail_once_at_start_ms=895_000)
    storage = InMemoryStorageClient({source_path: b"video"})
    service = NormalizationService(
        media=media,
        storage=storage,
        repository=repository,
        workdirs=WorkdirManager(tmp_path),
        part_duration_ms=900_000,
        overlap_ms=5_000,
        frame_interval_ms=600_000,
        frame_max_width=1280,
        frame_extraction_concurrency=2,
        stt_model_version="chirp-v3",
        signed_url_ttl_sec=8100,
    )
    message = NormalizeVideoMessage(
        message_type=MessageType.NORMALIZE_VIDEO,
        payload_version="v1",
        trace_id=uuid4(),
        attempt=1,
        pipeline_run_id=run_id,
        video_id=video_id,
        pipeline_version="pipeline-v1",
        issued_at=datetime.now(UTC),
    )

    with pytest.raises(RuntimeError, match="interrupted audio extraction"):
        await service.execute(message)
    await service.execute(message)

    async with session_factory() as session:
        part_count = len(
            list(
                await session.scalars(
                    select(PipelineAudioPartModel).where(
                        PipelineAudioPartModel.pipeline_run_id == run_id
                    )
                )
            )
        )
        run = await session.get(PipelineRunModel, run_id)

    assert media.part_ranges.count((0, 900_000)) == 1
    assert part_count == 2
    assert len(publisher.messages) == 2
    assert run is not None
    assert run.normalization_status == "COMPLETED"
