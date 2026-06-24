import json
from typing import Any

import asyncpg

from src.infra.broker import BrokerClient, BrokerPublishError, PublishableMessage


class PGMQBrokerClient(BrokerClient):
    def __init__(self, connection: Any | None = None, *, dsn: str | None = None) -> None:
        if connection is None and dsn is None:
            raise ValueError("PGMQBrokerClient requires either a live connection or a dsn.")
        self._connection = connection
        self._dsn = dsn

    async def publish(self, message: PublishableMessage) -> int | None:
        payload = message.to_payload()
        queue_name = message.queue_name or message.message_type
        try:
            message_id = await self._publish_payload(
                "SELECT pgmq.send(queue_name => $1, msg => $2::jsonb)",
                queue_name,
                json.dumps(payload),
            )
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
            raise BrokerPublishError("Failed to publish message to PGMQ.") from exc

        if message_id is None:
            raise BrokerPublishError("PGMQ did not return a message id.")

        return int(message_id)

    async def _publish_payload(self, query: str, queue_name: str, payload_json: str) -> int | None:
        if self._connection is not None:
            return await self._connection.fetchval(query, queue_name, payload_json)

        if self._dsn is None:
            raise ValueError("PGMQBrokerClient is missing database configuration.")

        connection = await asyncpg.connect(self._dsn)
        try:
            return await connection.fetchval(query, queue_name, payload_json)
        finally:
            await connection.close()
