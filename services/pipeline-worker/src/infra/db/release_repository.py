from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.db.artifact_repository import DEFAULT_VECTOR_INDEX_NAME
from src.infra.db.models import ModelReleaseModel


@dataclass(frozen=True, slots=True)
class EmbeddingTarget:
    index_name: str
    model_version: str


class RollbackPreparingError(RuntimeError):
    pass


class ReleaseContextRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_online_ingest_target(
        self,
        *,
        fallback_model_version: str,
        fallback_index_name: str = DEFAULT_VECTOR_INDEX_NAME,
    ) -> EmbeddingTarget:
        async with self._session_factory() as session:
            release = (
                await session.execute(
                    select(ModelReleaseModel).where(ModelReleaseModel.singleton_key == 1)
                )
            ).scalar_one_or_none()
        if release is None:
            return EmbeddingTarget(
                index_name=fallback_index_name,
                model_version=fallback_model_version,
            )
        if release.release_status == "ROLLBACK_PREPARING":
            raise RollbackPreparingError(
                "online ingest target is blocked while ModelRelease is ROLLBACK_PREPARING"
            )

        return EmbeddingTarget(
            index_name=release.active_index_name,
            model_version=release.active_model_version,
        )
