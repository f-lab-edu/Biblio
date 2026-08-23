from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.infra.db.models import PipelineAudioPartModel, PipelineRunModel, VideoModel

from src.telemetry.performance_sampler import (
    CgroupCounters,
    ProcessCounters,
    cgroup_counters,
    collect_cgroup_sample,
    collect_process_sample,
    collect_queue_sample,
    collect_stage_work_samples,
    performance_sampler_coroutines,
    _queue_sample_sql,
)


class _Connection:
    def __init__(self) -> None:
        self.query = ""

    async def fetchrow(self, query: str) -> dict[str, object]:
        self.query = query
        return {
            "timestamp_utc": datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            "ready_count": 3,
            "invisible_count": 2,
            "oldest_message_age_seconds": 7.5,
        }


class _Acquire:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _Connection:
        return self._connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _Pool:
    def __init__(self) -> None:
        self.connection = _Connection()

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


def test_collect_queue_sample_counts_ready_and_invisible_messages() -> None:
    pool = _Pool()

    sample = asyncio.run(collect_queue_sample(pool, "PREPROCESS_REQUEST"))

    assert sample.ready_count == 3
    assert sample.invisible_count == 2
    assert sample.oldest_message_age_seconds == pytest.approx(7.5)
    assert 'pgmq."q_preprocess_request"' in pool.connection.query
    assert "queue.vt <= observed.sampled_at" in pool.connection.query
    assert "FROM observed" in pool.connection.query
    assert "LEFT JOIN" in pool.connection.query


def test_queue_sample_sql_rejects_untrusted_identifier() -> None:
    with pytest.raises(ValueError, match="Unsupported PGMQ queue name"):
        _queue_sample_sql('PREPROCESS_REQUEST"; DROP TABLE video; --')


def test_collect_process_sample_calculates_cpu_and_current_rss(tmp_path) -> None:
    status_path = tmp_path / "status"
    status_path.write_text("Name:\tpython\nVmRSS:\t2048 kB\n", encoding="utf-8")

    sample = collect_process_sample(
        ProcessCounters(monotonic_seconds=10.0, process_cpu_seconds=2.0),
        ProcessCounters(monotonic_seconds=12.0, process_cpu_seconds=2.5),
        rss_path=status_path,
        now=lambda: datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
    )

    assert sample.cpu_percent == pytest.approx(25.0)
    assert sample.rss_bytes == 2 * 1024 * 1024
    assert sample.memory_bytes == 2 * 1024 * 1024
    assert sample.measurement_scope == "process"


def test_collect_cgroup_sample_uses_container_limits() -> None:
    sample = collect_cgroup_sample(
        CgroupCounters(10.0, 2.0, 1.0, 400, 1000),
        CgroupCounters(12.0, 2.5, 1.0, 500, 1000),
        now=lambda: datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
    )

    assert sample.cpu_percent == pytest.approx(25.0)
    assert sample.memory_bytes == 500
    assert sample.memory_percent == pytest.approx(50.0)
    assert sample.measurement_scope == "container-cgroup-v2"


def test_cgroup_counters_reads_v2_files(tmp_path) -> None:
    cpu_stat = tmp_path / "cpu.stat"
    cpu_max = tmp_path / "cpu.max"
    memory_current = tmp_path / "memory.current"
    memory_max = tmp_path / "memory.max"
    cpu_stat.write_text("usage_usec 2500000\n", encoding="utf-8")
    cpu_max.write_text("100000 100000\n", encoding="utf-8")
    memory_current.write_text("512\n", encoding="utf-8")
    memory_max.write_text("1024\n", encoding="utf-8")

    counters = cgroup_counters(
        cpu_stat_path=cpu_stat,
        cpu_max_path=cpu_max,
        memory_current_path=memory_current,
        memory_max_path=memory_max,
    )

    assert counters is not None
    assert counters.cpu_seconds == pytest.approx(2.5)
    assert counters.allocated_cpu_count == pytest.approx(1.0)
    assert counters.memory_bytes == 512
    assert counters.memory_limit_bytes == 1024


def test_performance_samplers_are_disabled_by_default() -> None:
    assert performance_sampler_coroutines(
        pgmq_pool=None,
        queue_names=("NORMALIZE_VIDEO", "TRANSCRIBE_PART"),
        stage_session_factory=None,
        queue_interval_seconds=0.0,
        process_interval_seconds=0.0,
    ) == []


@pytest.mark.asyncio
async def test_stage_work_samples_match_database_state(session_factory) -> None:
    video_id = uuid4()
    run_id = uuid4()
    ready_at = datetime(2026, 8, 14, 11, 59, 50, tzinfo=UTC)
    async with session_factory() as session:
        async with session.begin():
            session.add(
                VideoModel(
                    id=video_id,
                    user_id=uuid4(),
                    title="sample",
                    category="test",
                    input_type="GCS",
                    status="PROCESSING",
                )
            )
            session.add(
                PipelineRunModel(
                    id=run_id,
                    video_id=video_id,
                    pipeline_version="pipeline-v1",
                    normalization_status="RUNNING",
                )
            )
            session.add_all(
                [
                    PipelineAudioPartModel(
                        audio_part_id=uuid4(),
                        pipeline_run_id=run_id,
                        part_index=0,
                        start_ms=0,
                        end_ms=1000,
                        audio_gcs_path="artifacts/part-0.flac",
                        stt_model_version="chirp_3",
                        status="READY",
                        ready_at=ready_at,
                    ),
                    PipelineAudioPartModel(
                        audio_part_id=uuid4(),
                        pipeline_run_id=run_id,
                        part_index=1,
                        start_ms=1000,
                        end_ms=2000,
                        audio_gcs_path="artifacts/part-1.flac",
                        stt_model_version="chirp_3",
                        status="DISPATCHED",
                    ),
                ]
            )

    samples = await collect_stage_work_samples(
        session_factory,
        now=lambda: datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
    )

    transcription = next(
        sample for sample in samples if sample.stage == "TRANSCRIBE_PART"
    )
    assert transcription.ready_count == 1
    assert transcription.dispatched_count == 1
    assert transcription.running_count == 0
    assert transcription.oldest_ready_age_seconds == pytest.approx(10.0)
