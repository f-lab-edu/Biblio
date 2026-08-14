from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


@dataclass(frozen=True)
class QueueSample:
    timestamp_utc: str
    queue_name: str
    ready_count: int
    invisible_count: int
    oldest_message_age_seconds: float


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
    return ProcessSample(
        timestamp_utc=now().isoformat(),
        cpu_percent=max(cpu_percent, 0.0),
        rss_bytes=_current_rss_bytes(rss_path),
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


async def run_process_sampler(
    interval_seconds: float,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    logger = get_logger().bind(trace_id="-", video_id="-", user_id="-")
    previous = process_counters()
    while True:
        await sleep(interval_seconds)
        current = process_counters()
        sample = collect_process_sample(previous, current)
        previous = current
        logger.bind(**sample.__dict__).info(
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
            logger.bind(**sample.__dict__).info(
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


def performance_sampler_coroutines(
    *,
    pgmq_pool: Any | None,
    queue_name: str,
    queue_interval_seconds: float,
    process_interval_seconds: float,
) -> list[Awaitable[None]]:
    coroutines: list[Awaitable[None]] = []
    if pgmq_pool is not None and queue_interval_seconds > 0:
        coroutines.append(
            run_queue_sampler(pgmq_pool, queue_name, queue_interval_seconds)
        )
    if process_interval_seconds > 0:
        coroutines.append(run_process_sampler(process_interval_seconds))
    return coroutines


def _queue_sample_sql(queue_name: str) -> str:
    valid_characters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ_"
    if not queue_name or any(
        character not in valid_characters for character in queue_name
    ):
        raise ValueError(f"Unsupported PGMQ queue name: {queue_name!r}")
    table_name = f'q_{queue_name}'
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
        FROM pgmq."{table_name}" AS queue
        CROSS JOIN observed
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
