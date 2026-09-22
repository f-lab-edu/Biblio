from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from src.infra.db.models import (
    PipelineAudioPartModel,
    PipelineChunkWorkModel,
    PipelineEmbeddingBatchModel,
    PipelineRunModel,
)

from src.utils.logging import get_logger


@dataclass(frozen=True)
class ProcessCounters:
    monotonic_seconds: float
    process_cpu_seconds: float


@dataclass(frozen=True)
class ProcessSample:
    timestamp_utc: str
    cpu_percent: float
    rss_bytes: int
    memory_bytes: int = 0
    memory_limit_bytes: int | None = None
    memory_percent: float | None = None
    measurement_scope: str = "process"


@dataclass(frozen=True)
class CgroupCounters:
    monotonic_seconds: float
    cpu_seconds: float
    allocated_cpu_count: float
    memory_bytes: int
    memory_limit_bytes: int | None


@dataclass(frozen=True)
class QueueSample:
    timestamp_utc: str
    queue_name: str
    ready_count: int
    invisible_count: int
    oldest_message_age_seconds: float


@dataclass(frozen=True)
class StageWorkSample:
    timestamp_utc: str
    stage: str
    ready_count: int
    dispatched_count: int
    running_count: int
    failed_count: int
    oldest_ready_age_seconds: float


def process_counters() -> ProcessCounters:
    return ProcessCounters(time.monotonic(), time.process_time())


def collect_process_sample(
    previous: ProcessCounters,
    current: ProcessCounters,
    *,
    rss_path: Path = Path("/proc/self/status"),
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProcessSample:
    elapsed_seconds = current.monotonic_seconds - previous.monotonic_seconds
    cpu_seconds = current.process_cpu_seconds - previous.process_cpu_seconds
    cpu_percent = 0.0 if elapsed_seconds <= 0 else 100.0 * cpu_seconds / elapsed_seconds
    rss_bytes = _current_rss_bytes(rss_path)
    return ProcessSample(
        timestamp_utc=now().isoformat(),
        cpu_percent=max(cpu_percent, 0.0),
        rss_bytes=rss_bytes,
        memory_bytes=rss_bytes,
    )


def cgroup_counters(
    *,
    cpu_stat_path: Path = Path("/sys/fs/cgroup/cpu.stat"),
    cpu_max_path: Path = Path("/sys/fs/cgroup/cpu.max"),
    memory_current_path: Path = Path("/sys/fs/cgroup/memory.current"),
    memory_max_path: Path = Path("/sys/fs/cgroup/memory.max"),
) -> CgroupCounters | None:
    try:
        cpu_stat = _parse_key_value_file(cpu_stat_path)
        usage_seconds = int(cpu_stat["usage_usec"]) / 1_000_000
        allocated_cpu_count = _allocated_cpu_count(
            cpu_max_path.read_text(encoding="utf-8").strip()
        )
        memory_bytes = int(memory_current_path.read_text(encoding="utf-8").strip())
        memory_limit_bytes = _memory_limit(
            memory_max_path.read_text(encoding="utf-8").strip()
        )
    except (KeyError, OSError, ValueError):
        return None
    return CgroupCounters(
        monotonic_seconds=time.monotonic(),
        cpu_seconds=usage_seconds,
        allocated_cpu_count=allocated_cpu_count,
        memory_bytes=memory_bytes,
        memory_limit_bytes=memory_limit_bytes,
    )


def collect_cgroup_sample(
    previous: CgroupCounters,
    current: CgroupCounters,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProcessSample:
    elapsed_seconds = current.monotonic_seconds - previous.monotonic_seconds
    cpu_seconds = current.cpu_seconds - previous.cpu_seconds
    cpu_percent = (
        0.0
        if elapsed_seconds <= 0 or current.allocated_cpu_count <= 0
        else 100.0
        * cpu_seconds
        / elapsed_seconds
        / current.allocated_cpu_count
    )
    memory_percent = (
        None
        if current.memory_limit_bytes is None or current.memory_limit_bytes <= 0
        else 100.0 * current.memory_bytes / current.memory_limit_bytes
    )
    return ProcessSample(
        timestamp_utc=now().isoformat(),
        cpu_percent=max(cpu_percent, 0.0),
        rss_bytes=current.memory_bytes,
        memory_bytes=current.memory_bytes,
        memory_limit_bytes=current.memory_limit_bytes,
        memory_percent=memory_percent,
        measurement_scope="container-cgroup-v2",
    )


async def collect_queue_sample(pool: Any, queue_name: str) -> QueueSample:
    query = _queue_sample_sql(queue_name)
    async with pool.acquire() as connection:
        row = await connection.fetchrow(query)
    return QueueSample(
        timestamp_utc=row["timestamp_utc"].astimezone(UTC).isoformat(),
        queue_name=queue_name,
        ready_count=int(row["ready_count"]),
        invisible_count=int(row["invisible_count"]),
        oldest_message_age_seconds=float(row["oldest_message_age_seconds"]),
    )


async def collect_stage_work_samples(
    session_factory: Any,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[StageWorkSample, ...]:
    sampled_at = now()
    specs = (
        (
            "NORMALIZE_VIDEO",
            PipelineRunModel.normalization_status,
            PipelineRunModel.normalization_ready_at,
        ),
        (
            "TRANSCRIBE_PART",
            PipelineAudioPartModel.status,
            PipelineAudioPartModel.ready_at,
        ),
        (
            "ENRICH_CHUNK",
            PipelineChunkWorkModel.enrichment_status,
            PipelineChunkWorkModel.enrichment_ready_at,
        ),
        (
            "EMBED_BATCH",
            PipelineEmbeddingBatchModel.status,
            PipelineEmbeddingBatchModel.ready_at,
        ),
    )
    samples: list[StageWorkSample] = []
    async with session_factory() as session:
        for stage, status_column, ready_at_column in specs:
            row = (
                await session.execute(
                    select(
                        func.count().filter(status_column == "READY"),
                        func.count().filter(status_column == "DISPATCHED"),
                        func.count().filter(status_column == "RUNNING"),
                        func.count().filter(status_column == "FAILED"),
                        func.min(ready_at_column).filter(status_column == "READY"),
                    )
                )
            ).one()
            (
                ready_count,
                dispatched_count,
                running_count,
                failed_count,
                oldest_ready_at,
            ) = row
            oldest_ready_age_seconds = (
                max(
                    (sampled_at - _as_utc(oldest_ready_at)).total_seconds(),
                    0.0,
                )
                if oldest_ready_at is not None
                else 0.0
            )
            samples.append(
                StageWorkSample(
                    timestamp_utc=sampled_at.isoformat(),
                    stage=stage,
                    ready_count=int(ready_count),
                    dispatched_count=int(dispatched_count),
                    running_count=int(running_count),
                    failed_count=int(failed_count),
                    oldest_ready_age_seconds=oldest_ready_age_seconds,
                )
            )
    return tuple(samples)


async def run_process_sampler(
    interval_seconds: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    logger = get_logger().bind(trace_id="-", video_id="-", user_id="-")
    previous_cgroup = cgroup_counters()
    previous_process = process_counters()
    while True:
        await sleep(interval_seconds)
        current_cgroup = cgroup_counters()
        if previous_cgroup is not None and current_cgroup is not None:
            sample = collect_cgroup_sample(previous_cgroup, current_cgroup)
            previous_cgroup = current_cgroup
        else:
            current_process = process_counters()
            sample = collect_process_sample(previous_process, current_process)
            previous_process = current_process
        logger.bind(
            log_schema_version=2,
            event_name="worker.process.sample",
            **sample.__dict__,
        ).info(
            "worker.process.sample timestamp_utc={} cpu_percent={} rss_bytes={}",
            sample.timestamp_utc,
            sample.cpu_percent,
            sample.rss_bytes,
        )


async def run_queue_sampler(
    pool: Any,
    queue_name: str,
    interval_seconds: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    logger = get_logger().bind(trace_id="-", video_id="-", user_id="-")
    while True:
        try:
            sample = await collect_queue_sample(pool, queue_name)
            logger.bind(
                log_schema_version=2,
                event_name="queue.sample",
                **sample.__dict__,
            ).info(
                "queue.sample timestamp_utc={} queue={} ready={} invisible={} oldest_age_sec={}",
                sample.timestamp_utc,
                sample.queue_name,
                sample.ready_count,
                sample.invisible_count,
                sample.oldest_message_age_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("queue.sample.failed queue={}", queue_name)
        await sleep(interval_seconds)


async def run_stage_work_sampler(
    session_factory: Any,
    interval_seconds: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    logger = get_logger().bind(trace_id="-", video_id="-", user_id="-")
    while True:
        try:
            samples = await collect_stage_work_samples(session_factory)
            for sample in samples:
                logger.bind(
                    log_schema_version=2,
                    event_name="stage.work.sample",
                    **sample.__dict__,
                ).info("stage.work.sample")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("stage.work.sample.failed")
        await sleep(interval_seconds)


def performance_sampler_coroutines(
    *,
    pgmq_pool: Any | None,
    queue_names: Sequence[str],
    stage_session_factory: Any | None,
    queue_interval_seconds: float,
    process_interval_seconds: float,
) -> list[Awaitable[None]]:
    coroutines: list[Awaitable[None]] = []
    if pgmq_pool is not None and queue_interval_seconds > 0:
        coroutines.extend(
            run_queue_sampler(pgmq_pool, queue_name, queue_interval_seconds)
            for queue_name in queue_names
        )
    if stage_session_factory is not None and queue_interval_seconds > 0:
        coroutines.append(
            run_stage_work_sampler(
                stage_session_factory,
                queue_interval_seconds,
            )
        )
    if process_interval_seconds > 0:
        coroutines.append(run_process_sampler(process_interval_seconds))
    return coroutines


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _queue_sample_sql(queue_name: str) -> str:
    valid_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
    if not queue_name or any(
        character not in valid_characters for character in queue_name
    ):
        raise ValueError(f"Unsupported PGMQ queue name: {queue_name!r}")
    table_name = f"q_{queue_name.lower()}"
    return f"""
        WITH observed AS (SELECT clock_timestamp() AS sampled_at)
        SELECT
            observed.sampled_at AS timestamp_utc,
            COUNT(*) FILTER (WHERE queue.vt <= observed.sampled_at) AS ready_count,
            COUNT(*) FILTER (WHERE queue.vt > observed.sampled_at) AS invisible_count,
            COALESCE(
                EXTRACT(EPOCH FROM observed.sampled_at - MIN(queue.enqueued_at)),
                0
            ) AS oldest_message_age_seconds
        FROM observed
        LEFT JOIN pgmq."{table_name}" AS queue ON TRUE
        GROUP BY observed.sampled_at
    """


def _current_rss_bytes(path: Path) -> int:
    try:
        status_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in status_lines:
        if line.startswith("VmRSS:"):
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                return int(fields[1]) * 1024
    return 0


def _parse_key_value_file(path: Path) -> dict[str, str]:
    return {
        fields[0]: fields[1]
        for line in path.read_text(encoding="utf-8").splitlines()
        if len(fields := line.split()) == 2
    }


def _allocated_cpu_count(cpu_max: str) -> float:
    quota, period = cpu_max.split()
    if quota == "max":
        return float(os.cpu_count() or 1)
    period_value = int(period)
    if period_value <= 0:
        raise ValueError("cgroup CPU period must be positive")
    return max(int(quota) / period_value, 0.001)


def _memory_limit(raw_limit: str) -> int | None:
    if raw_limit == "max":
        return None
    return int(raw_limit)
