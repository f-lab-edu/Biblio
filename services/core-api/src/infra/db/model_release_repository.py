"""Skeleton model release repository.

Full transition logic is implemented in the release/reindex branch. This file
only fixes the read/load entry point so admin service skeletons can wire to a
stable shape.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.admin_ops import ModelRelease


class ModelReleaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, release_id: UUID) -> ModelRelease | None:
        result = await self.session.execute(
            select(ModelRelease).where(ModelRelease.id == release_id)
        )
        return result.scalar_one_or_none()

    async def get_current(self) -> ModelRelease | None:
        """Return the single `ModelRelease` row that is the SOT for serving state.

        Bootstrap timing (migration seed vs. service startup lazy init) is an
        open foundation question and resolved in the release/reindex branch.
        """
        result = await self.session.execute(
            select(ModelRelease).where(ModelRelease.singleton_key == 1)
        )
        return result.scalar_one_or_none()
