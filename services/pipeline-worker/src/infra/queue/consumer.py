import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal, Protocol

from loguru import logger

from src.infra.queue.broker import BrokerClient, BrokerMessage
from src.schemas.messages import (
    MessageEnvelope,
    MessageType,
    QueueMessage,
    StageMessage,
    StageMessageBase,
    parse_queue_message,
)
from src.telemetry.pipeline_events import (
    emit_pipeline_work_event,
    work_log_context_from_message,
)


@dataclass(frozen=True, slots=True)
class StageDispatchContext:
    message: StageMessage
    message_id: int
    read_count: int
    enqueued_at: datetime
    queue_name: str
    started_at: datetime
    queue_wait_ms: float


@dataclass(frozen=True, slots=True)
class StageHandlerResult:
    outcome: Literal["SUCCEEDED", "FAILED", "SKIPPED"]
    failure_code: str | None = None
    reason: str | None = None
    reused: bool = False


HandlerInput = MessageEnvelope | StageDispatchContext
MessageHandler = Callable[
    [HandlerInput],
    Awaitable[StageHandlerResult | None] | StageHandlerResult | None,
]


class StageMessageClaim(Protocol):
    should_execute: bool
    reason: str
    state_changed_at: datetime | None


class StageMessageClaimer(Protocol):
    async def claim_for_execution(
        self,
        message: StageMessage,
        message_id: int,
    ) -> StageMessageClaim: ...


@dataclass(frozen=True, slots=True)
class _ExecutableStageMessageClaim:
    should_execute: bool = True
    reason: str = "claimer_not_configured"
    state_changed_at: datetime | None = None


class MessageDispatchError(Exception):
    pass


class PipelineWorkerConsumer:
    def __init__(
        self,
        handlers: dict[MessageType, MessageHandler],
        *,
        stage_message_claimer: StageMessageClaimer | None = None,
    ) -> None:
        self._handlers = handlers.copy()
        self._stage_message_claimer = stage_message_claimer

    async def consume(
        self,
        raw_message: Mapping[str, Any] | MessageEnvelope,
    ) -> MessageEnvelope:
        message = (
            raw_message
            if isinstance(raw_message, MessageEnvelope)
            else MessageEnvelope.model_validate(dict(raw_message))
        )
        await self._dispatch(message, message)
        return message

    async def _dispatch(
        self,
        message: QueueMessage,
        handler_input: HandlerInput,
    ) -> StageHandlerResult | None:
        handler = self._handlers.get(message.message_type)
        if handler is None:
            logger.bind(
                trace_id=message.trace_id,
                video_id=self._target_label(message),
            ).error(
                "received queue message without handler for {}",
                message.message_type.value,
            )
            raise MessageDispatchError(
                f"No handler registered for {message.message_type.value}"
            )

        logger.bind(
            trace_id=message.trace_id,
            video_id=self._target_label(message),
        ).info(
            "dispatching queue message"
        )
        result = handler(handler_input)
        if inspect.isawaitable(result):
            return await result
        return result

    async def run_once(self, broker: BrokerClient, queue_name: str) -> bool:
        messages = await broker.consume(queue_name, limit=1)
        if not messages:
            return False

        message = messages[0]
        queue_message = parse_queue_message(message.payload)
        started_at = datetime.now(UTC)
        self._log_dispatch_started(
            envelope=queue_message,
            message=message,
            started_at=started_at,
        )
        if isinstance(queue_message, StageMessageBase):
            queue_wait_ms = (started_at - message.enqueued_at).total_seconds() * 1000
            context = StageDispatchContext(
                message=queue_message,
                message_id=self._parse_message_id(message.receipt_handle),
                read_count=message.read_ct,
                enqueued_at=message.enqueued_at,
                queue_name=queue_name,
                started_at=started_at,
                queue_wait_ms=queue_wait_ms,
            )
            claim = await self._claim_stage_message(context)
            if not claim.should_execute:
                await broker.ack(queue_name, message.receipt_handle)
                return True
            await self._dispatch_stage_message(
                queue_message,
                context,
                state_changed_at=getattr(claim, "state_changed_at", None),
            )
        else:
            await self._dispatch(queue_message, queue_message)
        await broker.ack(queue_name, message.receipt_handle)
        return True

    async def _dispatch_stage_message(
        self,
        message: StageMessage,
        context: StageDispatchContext,
        *,
        state_changed_at: datetime | None,
    ) -> None:
        work_context = work_log_context_from_message(
            message,
            message_id=context.message_id,
            read_ct=context.read_count,
        )
        emit_pipeline_work_event(
            "pipeline.work.started",
            work_context,
            timestamp_utc=state_changed_at or context.started_at,
            queue_wait_ms=context.queue_wait_ms,
        )
        execution_started_at = perf_counter()
        try:
            result = await self._dispatch(message, context)
        except Exception as error:
            emit_pipeline_work_event(
                "pipeline.work.retryable_failed",
                work_context,
                level="ERROR",
                execution_ms=(perf_counter() - execution_started_at) * 1000,
                failure_code=self._failure_code(error),
                retryable=True,
            )
            raise
        execution_ms = (perf_counter() - execution_started_at) * 1000
        if result is None or result.outcome == "SUCCEEDED":
            emit_pipeline_work_event(
                "pipeline.work.succeeded",
                work_context,
                execution_ms=execution_ms,
                reused=bool(result and result.reused),
            )
            return
        if result.outcome == "FAILED":
            emit_pipeline_work_event(
                "pipeline.work.failed",
                work_context,
                level="ERROR",
                execution_ms=execution_ms,
                failure_code=result.failure_code or "UNKNOWN",
                retryable=False,
            )
            return
        emit_pipeline_work_event(
            "pipeline.work.skipped",
            work_context,
            execution_ms=execution_ms,
            reason=result.reason or "handler_skipped",
        )

    async def _claim_stage_message(
        self,
        context: StageDispatchContext,
    ) -> StageMessageClaim:
        if self._stage_message_claimer is None:
            return _ExecutableStageMessageClaim()

        claim = await self._stage_message_claimer.claim_for_execution(
            context.message,
            context.message_id,
        )
        if not claim.should_execute:
            emit_pipeline_work_event(
                "pipeline.work.skipped",
                work_log_context_from_message(
                    context.message,
                    message_id=context.message_id,
                    read_ct=context.read_count,
                ),
                reason=claim.reason,
            )
        return claim

    async def run_until_empty(self, broker: BrokerClient, queue_names: list[str]) -> int:
        processed = 0
        keep_running = True
        while keep_running:
            keep_running = False
            for queue_name in queue_names:
                if await self.run_once(broker, queue_name):
                    processed += 1
                    keep_running = True
        return processed

    async def run_forever(
        self,
        broker: BrokerClient,
        queue_names: list[str],
        *,
        poll_interval_sec: float = 1.0,
    ) -> None:
        """Long-running consumer loop. Polls queues and sleeps when idle.

        Exits cleanly on asyncio.CancelledError (e.g. SIGINT via asyncio.run).
        """
        while True:
            processed_any = False
            for queue_name in queue_names:
                try:
                    if await self.run_once(broker, queue_name):
                        processed_any = True
                except Exception:
                    logger.exception("error processing message from {}", queue_name)
            if not processed_any:
                await asyncio.sleep(poll_interval_sec)

    def _log_dispatch_started(
        self,
        *,
        envelope: QueueMessage,
        message: BrokerMessage,
        started_at: datetime,
    ) -> None:
        queue_wait_ms = (
            started_at - message.enqueued_at
        ).total_seconds() * 1000

        logger.bind(
            trace_id=str(envelope.trace_id),
            video_id=self._target_label(envelope),
            attempt=envelope.attempt,
            read_ct=message.read_ct,
            queue_wait_ms=queue_wait_ms,
            issued_at=envelope.issued_at.astimezone(UTC).isoformat(),
            enqueued_at=message.enqueued_at.astimezone(UTC).isoformat(),
            started_at=started_at.astimezone(UTC).isoformat(),
        ).info(
            "queue.message.started queue_wait_ms={} attempt={} read_ct={}",
            queue_wait_ms,
            envelope.attempt,
            message.read_ct,
        )

    @staticmethod
    def _target_label(envelope: QueueMessage) -> str:
        if isinstance(envelope, MessageEnvelope):
            if envelope.video_ids:
                return ",".join(str(video_id) for video_id in envelope.video_ids)
            if envelope.project_id is not None:
                return str(envelope.project_id)
        video_id = getattr(envelope, "video_id", None)
        if video_id is not None:
            return str(video_id)
        return "-"

    @staticmethod
    def _parse_message_id(receipt_handle: str) -> int:
        try:
            return int(receipt_handle)
        except ValueError as error:
            raise MessageDispatchError(
                "Stage messages require a numeric PGMQ message id"
            ) from error

    @staticmethod
    def _failure_code(error: Exception) -> str:
        code = getattr(error, "code", None)
        if isinstance(code, str) and code.isupper():
            return code
        return type(error).__name__
