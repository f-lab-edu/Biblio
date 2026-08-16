from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from src.telemetry.performance_sampler import (
    ProcessCounters,
    collect_process_sample,
    collect_queue_sample,
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


def test_performance_samplers_are_disabled_by_default() -> None:
    assert performance_sampler_coroutines(
        pgmq_pool=None,
        queue_name="PREPROCESS_REQUEST",
        queue_interval_seconds=0.0,
        process_interval_seconds=0.0,
    ) == []
