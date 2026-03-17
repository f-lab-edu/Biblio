from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from loguru import logger

from adapters.queue.broker import BrokerClient
from schemas.messages import MessageEnvelope, MessageType

MessageHandler = Callable[[MessageEnvelope], Awaitable[None]]


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
                video_id=envelope.video_id,
            ).error("received queue message without handler for {}", envelope.message_type.value)
            raise MessageDispatchError(f"No handler registered for {envelope.message_type.value}")

        logger.bind(trace_id=envelope.trace_id, video_id=envelope.video_id).info(
            "dispatching queue message"
        )
        await handler(envelope)
        return envelope

    async def run_once(self, broker: BrokerClient, queue_name: str) -> bool:
        messages = await broker.consume(queue_name, limit=1)
        if not messages:
            return False

        message = messages[0]
        await self.consume(message.payload)
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
