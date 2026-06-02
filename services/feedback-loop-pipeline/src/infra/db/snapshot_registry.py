from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import ModelSnapshotModel


class ModelSnapshotStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_one(self, status: str) -> ModelSnapshotModel | None:
        result = await self._session.execute(
            select(ModelSnapshotModel).where(ModelSnapshotModel.status == status)
        )
        return result.scalar_one_or_none()

    async def record_cutover(
        self, *, model_version: str, index_name: str, captured_at: datetime
    ) -> ModelSnapshotModel:
        previous_stable = await self._get_one("PREVIOUS_STABLE")
        if previous_stable is not None:
            previous_stable.status = "SUPERSEDED"

        current_active = await self._get_one("ACTIVE")
        if current_active is not None:
            current_active.status = "PREVIOUS_STABLE"
        await self._session.flush()

        new_active = ModelSnapshotModel(
            model_version=model_version,
            index_name=index_name,
            status="ACTIVE",
            previous_snapshot_id=current_active.snapshot_id if current_active else None,
            captured_at=captured_at,
        )
        self._session.add(new_active)
        await self._session.flush()
        return new_active

    async def get_rollback_target(self) -> ModelSnapshotModel | None:
        return await self._get_one("PREVIOUS_STABLE")

    async def _latest_superseded(self) -> ModelSnapshotModel | None:
        result = await self._session.execute(
            select(ModelSnapshotModel)
            .where(ModelSnapshotModel.status == "SUPERSEDED")
            .order_by(ModelSnapshotModel.captured_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def record_rollback(self, *, restored_at: datetime) -> ModelSnapshotModel:
        target = await self._get_one("PREVIOUS_STABLE")
        if target is None:
            raise ValueError("no rollback target snapshot is available")

        current_active = await self._get_one("ACTIVE")
        if current_active is not None:
            current_active.status = "ROLLED_BACK"
        target.status = "SUPERSEDED"
        await self._session.flush()

        target.status = "ACTIVE"
        await self._session.flush()

        next_prior = await self._latest_superseded()
        if next_prior is not None:
            next_prior.status = "PREVIOUS_STABLE"
            await self._session.flush()
        return target
