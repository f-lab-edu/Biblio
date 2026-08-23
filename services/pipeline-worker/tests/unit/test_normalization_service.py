from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from src.infra.storage.inmemory_storage import InMemoryStorageClient
from src.schemas.messages import MessageType, NormalizeVideoMessage
from src.services.normalization_service import (
    FrameCandidate,
    NormalizationPart,
    NormalizationResumeState,
    NormalizationService,
    PersistedNormalizationPart,
    plan_frame_candidates,
    plan_normalization_parts,
)
from src.utils.workdir import WorkdirManager


class TestPlanNormalizationParts:
    def test_short_video_creates_one_part_ending_at_video_duration(self) -> None:
        video_id = uuid4()
        run_id = uuid4()

        parts = plan_normalization_parts(
            video_id=video_id,
            pipeline_run_id=run_id,
            duration_ms=60_000,
            part_duration_ms=900_000,
            overlap_ms=5_000,
        )

        assert [(part.start_ms, part.end_ms) for part in parts] == [(0, 60_000)]
        assert parts[0].storage_path == (
            f"artifacts/{video_id}/pipeline-runs/{run_id}/"
            "audio-parts/part-000-0-60000.flac"
        )

    def test_long_video_applies_overlap_after_first_part(self) -> None:
        parts = plan_normalization_parts(
            video_id=uuid4(),
            pipeline_run_id=uuid4(),
            duration_ms=2_700_000,
            part_duration_ms=900_000,
            overlap_ms=5_000,
        )

        assert [
            (part.part_index, part.start_ms, part.end_ms)
            for part in parts
        ] == [
            (0, 0, 900_000),
            (1, 895_000, 1_800_000),
            (2, 1_795_000, 2_700_000),
        ]

    @pytest.mark.parametrize(
        ("duration_ms", "part_duration_ms", "overlap_ms"),
        [
            (0, 900_000, 5_000),
            (60_000, 0, 0),
            (60_000, 10_000, -1),
            (60_000, 10_000, 10_000),
        ],
    )
    def test_rejects_invalid_ranges(
        self,
        duration_ms: int,
        part_duration_ms: int,
        overlap_ms: int,
    ) -> None:
        with pytest.raises(ValueError):
            plan_normalization_parts(
                video_id=uuid4(),
                pipeline_run_id=uuid4(),
                duration_ms=duration_ms,
                part_duration_ms=part_duration_ms,
                overlap_ms=overlap_ms,
            )


class TestPlanFrameCandidates:
    def test_uses_fixed_interval_and_run_scoped_paths(self) -> None:
        video_id = uuid4()
        run_id = uuid4()

        frames = plan_frame_candidates(
            video_id=video_id,
            pipeline_run_id=run_id,
            duration_ms=125_000,
            interval_ms=60_000,
        )

        assert [frame.timestamp_ms for frame in frames] == [30_000, 90_000]
        assert frames[-1].storage_path == (
            f"artifacts/{video_id}/pipeline-runs/{run_id}/"
            "frame-candidates/frame-00001-90000.jpg"
        )

    def test_short_video_uses_its_midpoint(self) -> None:
        frames = plan_frame_candidates(
            video_id=uuid4(),
            pipeline_run_id=uuid4(),
            duration_ms=10_000,
            interval_ms=60_000,
        )

        assert [frame.timestamp_ms for frame in frames] == [5_000]


class _FakeMedia:
    def __init__(self, duration_ms: int) -> None:
        self.duration_ms = duration_ms
        self.part_ranges: list[tuple[int, int]] = []
        self.frame_offsets: list[int] = []

    def probe_duration_ms(self, input_file: Path | str) -> int:
        assert str(input_file).startswith("https://storage.test/")
        return self.duration_ms

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
        output_file.write_bytes(f"audio:{start_ms}:{end_ms}".encode())

    def extract_frame_candidates(
        self,
        input_file: Path | str,
        output_pattern: Path,
        *,
        first_offset_ms: int,
        interval_ms: int,
        frame_count: int,
        max_width: int,
    ) -> None:
        assert str(input_file).startswith("https://storage.test/")
        for output_index in range(frame_count):
            offset_ms = first_offset_ms + output_index * interval_ms
            self.frame_offsets.append(offset_ms)
            output_file = Path(
                str(output_pattern).replace("%05d", f"{output_index:05d}")
            )
            output_file.write_bytes(f"frame:{offset_ms}:{max_width}".encode())


class _FakeRepository:
    def __init__(self, source_path: str) -> None:
        self.source_path = source_path
        self.parts: list[NormalizationPart] = []
        self.frames: list[tuple[int, int, str]] = []
        self.completed_counts: list[int] = []
        self.completed_frame_counts: list[int] = []
        self.reject_part_index: int | None = None
        self.active = True
        self.deactivate_on_reject = False
        self.source_generation: str | None = None

    async def get_resume_state(self, **_kwargs) -> NormalizationResumeState | None:
        if not self.active:
            return None
        return NormalizationResumeState(
            source_path=self.source_path,
            source_generation=self.source_generation,
            parts=tuple(
                PersistedNormalizationPart(
                    part_index=part.part_index,
                    start_ms=part.start_ms,
                    end_ms=part.end_ms,
                    storage_path=part.storage_path,
                    stt_model_version="chirp-v3",
                    status="DISPATCHED",
                )
                for part in self.parts
            ),
            frames=tuple(
                FrameCandidate(
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    storage_path=storage_path,
                )
                for frame_index, timestamp_ms, storage_path in self.frames
            ),
        )

    async def should_discard_artifacts(self, **_kwargs) -> bool:
        return not self.active

    async def bind_source_identity(self, *, generation: str, **_kwargs) -> bool:
        if not self.active:
            return False
        self.source_generation = generation
        return True

    async def complete_part_and_dispatch(self, *, part, **_kwargs) -> bool:
        if part.part_index == self.reject_part_index:
            if self.deactivate_on_reject:
                self.active = False
            return False
        self.parts.append(part)
        return True

    async def save_frame_candidate(
        self,
        *,
        frame_index: int,
        timestamp_ms: int,
        frame_gcs_path: str,
        **_kwargs,
    ) -> bool:
        self.frames.append((frame_index, timestamp_ms, frame_gcs_path))
        return True

    async def complete_normalization(
        self,
        *,
        total_part_count: int,
        total_frame_count: int,
        **_kwargs,
    ) -> bool:
        self.completed_counts.append(total_part_count)
        self.completed_frame_counts.append(total_frame_count)
        return True


class _DeactivateAfterProbeStorage(InMemoryStorageClient):
    def __init__(
        self,
        objects: dict[str, bytes],
        repository: _FakeRepository,
    ) -> None:
        super().__init__(objects)
        self._repository = repository

    def create_media_input(self, storage_path: str, **kwargs):
        media_input = super().create_media_input(storage_path, **kwargs)
        self._repository.active = False
        return media_input


class _StreamingOnlyStorage(InMemoryStorageClient):
    async def download_object(self, storage_path: str, destination: Path) -> None:
        del storage_path, destination
        raise AssertionError("normalization must not download the whole source")


def _message() -> NormalizeVideoMessage:
    return NormalizeVideoMessage(
        message_type=MessageType.NORMALIZE_VIDEO,
        payload_version="v1",
        trace_id=uuid4(),
        attempt=1,
        pipeline_run_id=uuid4(),
        video_id=uuid4(),
        pipeline_version="pipeline-v1",
        issued_at=datetime.now(UTC),
    )


def _service(
    *,
    media: _FakeMedia,
    storage: InMemoryStorageClient,
    repository: _FakeRepository,
    workdir: Path,
) -> NormalizationService:
    return NormalizationService(
        media=media,
        storage=storage,
        repository=repository,
        workdirs=WorkdirManager(workdir),
        part_duration_ms=900_000,
        overlap_ms=5_000,
        frame_interval_ms=60_000,
        frame_max_width=1280,
        stt_model_version="chirp-v3",
        signed_url_ttl_sec=8100,
    )


class TestNormalizationService:
    @pytest.mark.asyncio
    async def test_creates_parts_frames_and_completes_after_persistence(
        self,
        tmp_path: Path,
    ) -> None:
        message = _message()
        source_path = "videos/source.mp4"
        storage = _StreamingOnlyStorage({source_path: b"video"})
        media = _FakeMedia(duration_ms=1_020_000)
        repository = _FakeRepository(source_path)

        await _service(
            media=media,
            storage=storage,
            repository=repository,
            workdir=tmp_path,
        ).execute(message)

        assert media.part_ranges == [(0, 900_000), (895_000, 1_020_000)]
        assert repository.completed_counts == [2]
        assert repository.completed_frame_counts == [17]
        assert len(repository.parts) == 2
        assert len(repository.frames) == 17
        assert list((tmp_path / "pipeline_worker_workdirs").iterdir()) == []

    @pytest.mark.asyncio
    async def test_deletion_after_probe_skips_ffmpeg(
        self,
        tmp_path: Path,
    ) -> None:
        message = _message()
        source_path = "videos/source.mp4"
        repository = _FakeRepository(source_path)
        storage = _DeactivateAfterProbeStorage(
            {source_path: b"video"},
            repository,
        )
        media = _FakeMedia(duration_ms=60_000)

        await _service(
            media=media,
            storage=storage,
            repository=repository,
            workdir=tmp_path,
        ).execute(message)

        assert media.part_ranges == []
        assert media.frame_offsets == []
        assert set(storage.objects) == {source_path}
        assert list((tmp_path / "pipeline_worker_workdirs").iterdir()) == []

    @pytest.mark.asyncio
    async def test_resume_skips_persisted_parts_and_frames(
        self,
        tmp_path: Path,
    ) -> None:
        message = _message()
        source_path = "videos/source.mp4"
        storage = InMemoryStorageClient({source_path: b"video"})
        media = _FakeMedia(duration_ms=125_000)
        repository = _FakeRepository(source_path)
        planned_parts = plan_normalization_parts(
            video_id=message.video_id,
            pipeline_run_id=message.pipeline_run_id,
            duration_ms=125_000,
            part_duration_ms=900_000,
            overlap_ms=5_000,
        )
        planned_frames = plan_frame_candidates(
            video_id=message.video_id,
            pipeline_run_id=message.pipeline_run_id,
            duration_ms=125_000,
            interval_ms=60_000,
        )
        repository.parts.append(planned_parts[0])
        repository.frames.append(
            (
                planned_frames[0].frame_index,
                planned_frames[0].timestamp_ms,
                planned_frames[0].storage_path,
            )
        )

        await _service(
            media=media,
            storage=storage,
            repository=repository,
            workdir=tmp_path,
        ).execute(message)

        assert media.part_ranges == []
        assert media.frame_offsets == [90_000]
        assert len(repository.parts) == 1
        assert len(repository.frames) == 2

    @pytest.mark.asyncio
    async def test_deletion_fence_rejection_removes_uploaded_artifacts(
        self,
        tmp_path: Path,
    ) -> None:
        message = _message()
        source_path = "videos/source.mp4"
        storage = InMemoryStorageClient({source_path: b"video"})
        media = _FakeMedia(duration_ms=1_020_000)
        repository = _FakeRepository(source_path)
        repository.reject_part_index = 1
        repository.deactivate_on_reject = True

        await _service(
            media=media,
            storage=storage,
            repository=repository,
            workdir=tmp_path,
        ).execute(message)

        assert set(storage.objects) == {source_path}
        assert repository.completed_counts == []
        assert repository.frames == []
        assert list((tmp_path / "pipeline_worker_workdirs").iterdir()) == []

    @pytest.mark.asyncio
    async def test_terminal_part_does_not_delete_previous_persisted_artifacts(
        self,
        tmp_path: Path,
    ) -> None:
        message = _message()
        source_path = "videos/source.mp4"
        storage = InMemoryStorageClient({source_path: b"video"})
        repository = _FakeRepository(source_path)
        repository.reject_part_index = 1

        await _service(
            media=_FakeMedia(duration_ms=1_020_000),
            storage=storage,
            repository=repository,
            workdir=tmp_path,
        ).execute(message)

        artifact_paths = set(storage.objects) - {source_path}
        assert len(artifact_paths) == 2
        assert repository.completed_counts == []
