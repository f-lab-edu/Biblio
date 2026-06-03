from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from src.runtime.queue import InMemoryBrokerClient
from src.runtime.scheduler import FeedbackLoopScheduler


@dataclass
class _Report:
    stuck_run_ids: list[str]
    rollback_stuck: bool
    release_status: str


class _Reconciliation:
    def __init__(self) -> None:
        self.calls: list[dict[str, int]] = []

    async def inspect(self, *, stuck_run_timeout_sec: int, rollback_timeout_sec: int) -> _Report:
        await asyncio.sleep(0)
        self.calls.append(
            {
                "stuck_run_timeout_sec": stuck_run_timeout_sec,
                "rollback_timeout_sec": rollback_timeout_sec,
            }
        )
        return _Report(stuck_run_ids=[], rollback_stuck=False, release_status="STABLE")


async def test_scheduler_run_once_enqueues_dataset_and_training_requests() -> None:
    broker = InMemoryBrokerClient()
    reconciliation = _Reconciliation()
    scheduler = FeedbackLoopScheduler(
        broker=broker,
        reconciliation=reconciliation,
        dataset_queue_name="feedback.dataset",
        training_queue_name="feedback.training",
        stuck_run_timeout_sec=3600,
        rollback_timeout_sec=300,
        now_provider=lambda: datetime(2026, 5, 29, 3, 0, tzinfo=UTC),
    )

    await scheduler.run_once()

    dataset_messages = await broker.consume("feedback.dataset", limit=10)
    training_messages = await broker.consume("feedback.training", limit=10)
    assert [message.payload["message_type"] for message in dataset_messages] == [
        "DATASET_GENERATION_REQUEST"
    ]
    assert [message.payload["message_type"] for message in training_messages] == [
        "TRAINING_REQUEST"
    ]
    assert reconciliation.calls == [
        {"stuck_run_timeout_sec": 3600, "rollback_timeout_sec": 300}
    ]


async def test_scheduler_run_tick_only_enqueues_due_work_once_per_window() -> None:
    broker = InMemoryBrokerClient()
    reconciliation = _Reconciliation()
    current_time = datetime(2026, 6, 1, 3, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    scheduler = FeedbackLoopScheduler(
        broker=broker,
        reconciliation=reconciliation,
        dataset_queue_name="feedback.dataset",
        training_queue_name="feedback.training",
        stuck_run_timeout_sec=3600,
        rollback_timeout_sec=300,
        dataset_hour_kst=3,
        dataset_minute_kst=0,
        training_weekday_kst="mon",
        training_hour_kst=3,
        training_minute_kst=0,
        now_provider=lambda: current_time,
    )

    await scheduler.run_tick()
    await scheduler.run_tick()

    dataset_messages = await broker.consume("feedback.dataset", limit=10)
    training_messages = await broker.consume("feedback.training", limit=10)
    assert len(dataset_messages) == 1
    assert len(training_messages) == 1
    assert len(reconciliation.calls) == 2


async def test_scheduler_run_tick_runs_reconciliation_when_work_is_not_due() -> None:
    broker = InMemoryBrokerClient()
    reconciliation = _Reconciliation()
    scheduler = FeedbackLoopScheduler(
        broker=broker,
        reconciliation=reconciliation,
        dataset_queue_name="feedback.dataset",
        training_queue_name="feedback.training",
        stuck_run_timeout_sec=3600,
        rollback_timeout_sec=300,
        dataset_hour_kst=3,
        dataset_minute_kst=0,
        training_weekday_kst="mon",
        training_hour_kst=4,
        training_minute_kst=0,
        now_provider=lambda: datetime(2026, 6, 1, 2, 59, tzinfo=ZoneInfo("Asia/Seoul")),
    )

    await scheduler.run_tick()

    assert await broker.consume("feedback.dataset", limit=10) == []
    assert await broker.consume("feedback.training", limit=10) == []
    assert len(reconciliation.calls) == 1


@pytest.mark.asyncio
async def test_run_once_invokes_recovery() -> None:
    class _RecoverySpy:
        def __init__(self) -> None:
            self.calls = 0

        async def scan_and_recover(self) -> None:
            self.calls += 1

    spy = _RecoverySpy()
    broker = InMemoryBrokerClient()
    reconciliation = _Reconciliation()
    scheduler = FeedbackLoopScheduler(
        broker=broker,
        reconciliation=reconciliation,
        dataset_queue_name="feedback.dataset",
        training_queue_name="feedback.training",
        stuck_run_timeout_sec=3600,
        rollback_timeout_sec=300,
        now_provider=lambda: datetime(2026, 5, 29, 3, 0, tzinfo=UTC),
        recovery=spy,
    )
    await scheduler.run_once()
    assert spy.calls == 1


@pytest.mark.asyncio
async def test_run_once_invokes_candidate_deployment_retry() -> None:
    class _CandidateDeploymentSpy:
        def __init__(self) -> None:
            self.calls = 0

        async def scan_and_deploy(self) -> None:
            self.calls += 1

    spy = _CandidateDeploymentSpy()
    broker = InMemoryBrokerClient()
    reconciliation = _Reconciliation()
    scheduler = FeedbackLoopScheduler(
        broker=broker,
        reconciliation=reconciliation,
        dataset_queue_name="feedback.dataset",
        training_queue_name="feedback.training",
        stuck_run_timeout_sec=3600,
        rollback_timeout_sec=300,
        now_provider=lambda: datetime(2026, 5, 29, 3, 0, tzinfo=UTC),
        candidate_deployment=spy,
    )

    await scheduler.run_once()

    assert spy.calls == 1
