from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelReleaseSnapshot:
    active_model_version: str
    previous_model_version: str | None = None
    candidate_model_version: str | None = None


class ModelReleaseRepository(Protocol):
    async def get_current(self) -> ModelReleaseSnapshot | None:
        """Return current ModelRelease serving model versions."""
        ...


class NullModelReleaseRepository:
    async def get_current(self) -> ModelReleaseSnapshot | None:
        return None


# ModelRelease SOT를 endpoint runtime reload에 연결하는 읽기 전용 repository
class AsyncpgModelReleaseRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    async def get_current(self) -> ModelReleaseSnapshot | None:
        import asyncpg

        conn = await asyncpg.connect(_normalize_database_url(self._database_url))
        try:
            row = await conn.fetchrow(
                """
                SELECT active_model_version,
                       previous_model_version,
                       candidate_model_version
                FROM model_release
                WHERE singleton_key = 1
                """
            )
        finally:
            await conn.close()

        if row is None:
            return None
        return ModelReleaseSnapshot(
            active_model_version=row["active_model_version"],
            previous_model_version=row["previous_model_version"],
            candidate_model_version=row["candidate_model_version"],
        )


def _normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
