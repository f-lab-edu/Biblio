from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from loguru import logger

from src.runtime.messages import build_dataset_generation_request, build_training_request
from src.runtime.queue import BrokerClient


class ReconciliationPort(Protocol):
    async def inspect(self, *, stuck_run_timeout_sec: int, rollback_timeout_sec: int) -> object: ...


class RecoveryPort(Protocol):
    async def scan_and_recover(self) -> None: ...


class FeedbackLoopScheduler:
    _KST = ZoneInfo("Asia/Seoul")
    _WEEKDAY_INDEX = {
        "mon": 0,
        "tue": 1,
        "wed": 2,
        "thu": 3,
        "fri": 4,
        "sat": 5,
        "sun": 6,
    }

    def __init__(
        self,
        *,
        broker: BrokerClient,
        reconciliation: ReconciliationPort,
        dataset_queue_name: str,
        training_queue_name: str,
        stuck_run_timeout_sec: int,
        rollback_timeout_sec: int,
        dataset_hour_kst: int = 3,
        dataset_minute_kst: int = 0,
        training_weekday_kst: str = "mon",
        training_hour_kst: int = 4,
        training_minute_kst: int = 0,
        now_provider: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        recovery: RecoveryPort | None = None,
    ) -> None:
        self._broker = broker
        self._reconciliation = reconciliation
        self._dataset_queue_name = dataset_queue_name
        self._training_queue_name = training_queue_name
        self._stuck_run_timeout_sec = stuck_run_timeout_sec
        self._rollback_timeout_sec = rollback_timeout_sec
        self._dataset_hour_kst = dataset_hour_kst
        self._dataset_minute_kst = dataset_minute_kst
        self._training_weekday_kst = training_weekday_kst
        self._training_hour_kst = training_hour_kst
        self._training_minute_kst = training_minute_kst
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._sleep = sleep
        self._recovery = recovery
        self._last_dataset_date: str | None = None
        self._last_training_week: tuple[int, int] | None = None

    async def run_once(self) -> None:
        now = self._now_provider()
        await self._enqueue_dataset_generation(now)
        await self._enqueue_training_request(now)
        await self._inspect_reconciliation()
        if self._recovery is not None:
            await self._recovery.scan_and_recover()
        logger.info("feedback-loop scheduler run-once completed")

    async def run_tick(self) -> None:
        now = self._now_provider()
        if self._is_dataset_due(now):
            await self._enqueue_dataset_generation(now)
        if self._is_training_due(now):
            await self._enqueue_training_request(now)
        await self._inspect_reconciliation()
        if self._recovery is not None:
            await self._recovery.scan_and_recover()
        logger.info("feedback-loop scheduler tick completed")

    async def run_forever(self, *, tick_interval_sec: int) -> None:
        while True:
            await self.run_tick()
            await self._sleep(tick_interval_sec)

    async def _enqueue_dataset_generation(self, now: datetime) -> None:
        await self._broker.enqueue(
            self._dataset_queue_name,
            build_dataset_generation_request(trace_id=uuid4(), issued_at=now),
        )
        self._last_dataset_date = now.astimezone(self._KST).date().isoformat()

    async def _enqueue_training_request(self, now: datetime) -> None:
        await self._broker.enqueue(
            self._training_queue_name,
            build_training_request(trace_id=uuid4(), issued_at=now),
        )
        calendar = now.astimezone(self._KST).isocalendar()
        self._last_training_week = (calendar.year, calendar.week)

    async def _inspect_reconciliation(self) -> None:
        report = await self._reconciliation.inspect(
            stuck_run_timeout_sec=self._stuck_run_timeout_sec,
            rollback_timeout_sec=self._rollback_timeout_sec,
        )
        logger.bind(reconciliation_report=str(report)).info("feedback-loop reconciliation inspected")

    def _is_dataset_due(self, now: datetime) -> bool:
        candidate = now.astimezone(self._KST)
        if candidate.hour != self._dataset_hour_kst or candidate.minute != self._dataset_minute_kst:
            return False
        today = candidate.date().isoformat()
        return self._last_dataset_date != today

    def _is_training_due(self, now: datetime) -> bool:
        candidate = now.astimezone(self._KST)
        if candidate.weekday() != self._WEEKDAY_INDEX[self._training_weekday_kst]:
            return False
        if candidate.hour != self._training_hour_kst or candidate.minute != self._training_minute_kst:
            return False
        calendar = candidate.isocalendar()
        return self._last_training_week != (calendar.year, calendar.week)
