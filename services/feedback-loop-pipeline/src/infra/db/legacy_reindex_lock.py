from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PostgresAdvisoryLegacyReindexLock:
    def __init__(
        self,
        session: AsyncSession,
        *,
        lock_name: str = "legacy_reembedding_global_lock",
    ) -> None:
        self._session = session
        self._lock_name = lock_name
        self._acquired = False

    async def try_acquire(self) -> bool:
        if self._dialect_name() != "postgresql":
            self._acquired = True
            return True

        result = await self._session.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:lock_name))"),
            {"lock_name": self._lock_name},
        )
        self._acquired = bool(result.scalar())
        return self._acquired

    async def release(self) -> None:
        if not self._acquired:
            return
        if self._dialect_name() == "postgresql":
            await self._session.execute(
                text("SELECT pg_advisory_unlock(hashtext(:lock_name))"),
                {"lock_name": self._lock_name},
            )
        self._acquired = False

    def _dialect_name(self) -> str:
        return self._session.get_bind().dialect.name
