import json
from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class TransactionalPGMQPublisher:
    async def send(
        self,
        session: AsyncSession,
        queue_name: str,
        payload: Mapping[str, object],
    ) -> int:
        result = await session.execute(
            text("SELECT pgmq.send(:queue_name, CAST(:payload AS jsonb))"),
            {
                "queue_name": queue_name,
                "payload": json.dumps(payload),
            },
        )
        return int(result.scalar_one())
