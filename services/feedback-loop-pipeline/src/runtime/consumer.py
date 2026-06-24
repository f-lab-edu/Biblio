from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from loguru import logger

from src.runtime.queue import BrokerClient


MessageHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class MessageDispatchError(Exception):
    pass


class QueueMessageConsumer:
    def __init__(self, handlers: Mapping[str, MessageHandler]) -> None:
        self._handlers = dict(handlers)

    async def consume(self, payload: dict[str, Any]) -> dict[str, Any]:
        message_type = str(payload.get("message_type", ""))
        handler = self._handlers.get(message_type)
        if handler is None:
            raise MessageDispatchError(f"No handler registered for {message_type}")
        result = handler(payload)
        if inspect.isawaitable(result):
            await result
        return payload

    async def run_once(self, broker: BrokerClient, queue_name: str) -> bool:
        messages = await broker.consume(queue_name, limit=1)
        if not messages:
            return False
        message = messages[0]
        await self.consume(message.payload)
        await broker.ack(queue_name, message.receipt_handle)
        return True

    async def run_forever(
        self,
        broker: BrokerClient,
        queue_name: str,
        *,
        poll_interval_sec: float,
    ) -> None:
        while True:
            try:
                processed = await self.run_once(broker, queue_name)
            except Exception:
                logger.exception("error processing message from {}", queue_name)
                processed = False
            if not processed:
                await asyncio.sleep(poll_interval_sec)
