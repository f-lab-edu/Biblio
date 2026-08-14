import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from src.infra.queue.broker import BrokerClient, BrokerMessage
from src.schemas.messages import MessageEnvelope, MessageType

MessageHandler = Callable[[MessageEnvelope], Awaitable[None] | None]


class MessageDispatchError(Exception):
    pass


class PipelineWorkerConsumer:
    def __init__(self, handlers: dict[MessageType, MessageHandler]) -> None:
        self._handlers = handlers.copy()

    async def consume(self, raw_message: Mapping[str, Any] | MessageEnvelope) -> MessageEnvelope:
        envelope = (
            raw_message
            if isinstance(raw_message, MessageEnvelope)
            else MessageEnvelope.model_validate(dict(raw_message))
        )
        handler = self._handlers.get(envelope.message_type)
        if handler is None:
            logger.bind(
                trace_id=envelope.trace_id,
                video_id=self._target_label(envelope),
            ).error("received queue message without handler for {}", envelope.message_type.value)
            raise MessageDispatchError(f"No handler registered for {envelope.message_type.value}")

        logger.bind(
            trace_id=envelope.trace_id,
            video_id=self._target_label(envelope),
        ).info(
            "dispatching queue message"
        )
        result = handler(envelope)
        if inspect.isawaitable(result):
            await result
        return envelope

    async def run_once(self, broker: BrokerClient, queue_name: str) -> bool:
        messages = await broker.consume(queue_name, limit=1)
        if not messages:
            return False

        message = messages[0]
        envelope = MessageEnvelope.model_validate(message.payload)
        self._log_dispatch_started(
            envelope=envelope,
            message=message,
            started_at=datetime.now(UTC),
        )
        await self.consume(envelope)
        await broker.ack(queue_name, message.receipt_handle)
        return True

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
        envelope: MessageEnvelope,
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
    def _target_label(envelope: MessageEnvelope) -> str:
        if envelope.video_ids:
            return ",".join(str(video_id) for video_id in envelope.video_ids)
        if envelope.project_id is not None:
            return str(envelope.project_id)
        return "-"
