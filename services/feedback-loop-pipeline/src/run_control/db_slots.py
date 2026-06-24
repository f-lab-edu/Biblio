from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.models import MLPipelineRunModel


ACTIVE_RUN_STATUSES = ("RUNNING", "PENDING")


@dataclass(frozen=True)
class DbRunSlotDecision:
    run: MLPipelineRunModel
    created: bool
    should_execute_now: bool


def candidate_model_version_for_run(run_id: UUID) -> str:
    return f"candidate-{run_id}"


class DbRunSlotStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def request_training_run(
        self,
        *,
        dataset_version: str,
        baseline_model_version: str,
        requested_at: datetime,
    ) -> DbRunSlotDecision:
        try:
            async with self._session.begin_nested():
                return await self._request_training_run_once(
                    dataset_version=dataset_version,
                    baseline_model_version=baseline_model_version,
                    requested_at=requested_at,
                )
        except IntegrityError:
            return await self._decision_from_current_slot(dataset_version)

    async def _request_training_run_once(
        self,
        *,
        dataset_version: str,
        baseline_model_version: str,
        requested_at: datetime,
    ) -> DbRunSlotDecision:
        active_for_dataset = await self._find_active_for_dataset(dataset_version)
        if active_for_dataset is not None:
            return DbRunSlotDecision(
                run=active_for_dataset,
                created=False,
                should_execute_now=active_for_dataset.status == "RUNNING",
            )

        running = await self._find_one_by_status("RUNNING")
        if running is None:
            run = self._new_run(
                status="RUNNING",
                dataset_version=dataset_version,
                baseline_model_version=baseline_model_version,
                requested_at=requested_at,
            )
            self._session.add(run)
            await self._session.flush()
            return DbRunSlotDecision(run=run, created=True, should_execute_now=True)

        pending = await self._find_one_by_status("PENDING")
        run = self._new_run(
            status="PENDING",
            dataset_version=dataset_version,
            baseline_model_version=baseline_model_version,
            requested_at=requested_at,
        )
        self._session.add(run)
        if pending is not None:
            pending.status = "SUPERSEDED"
            pending.updated_at = requested_at
        await self._session.flush()
        if pending is not None:
            pending.superseded_by_run_id = run.id
            await self._session.flush()
        return DbRunSlotDecision(run=run, created=True, should_execute_now=False)

    async def _decision_from_current_slot(self, dataset_version: str) -> DbRunSlotDecision:
        active_for_dataset = await self._read_active_for_dataset(dataset_version)
        if active_for_dataset is not None:
            return DbRunSlotDecision(
                run=active_for_dataset,
                created=False,
                should_execute_now=active_for_dataset.status == "RUNNING",
            )
        running = await self._read_one_by_status("RUNNING")
        if running is not None:
            return DbRunSlotDecision(run=running, created=False, should_execute_now=True)
        pending = await self._read_one_by_status("PENDING")
        if pending is not None:
            return DbRunSlotDecision(run=pending, created=False, should_execute_now=False)
        raise RuntimeError("run slot conflict could not be resolved")

    async def _find_active_for_dataset(self, dataset_version: str) -> MLPipelineRunModel | None:
        result = await self._session.execute(
            select(MLPipelineRunModel)
            .where(
                MLPipelineRunModel.dataset_version == dataset_version,
                MLPipelineRunModel.status.in_(ACTIVE_RUN_STATUSES),
            )
            .order_by(MLPipelineRunModel.created_at.asc())
            .with_for_update()
        )
        return result.scalars().first()

    async def _find_one_by_status(self, status: str) -> MLPipelineRunModel | None:
        result = await self._session.execute(
            select(MLPipelineRunModel)
            .where(MLPipelineRunModel.status == status)
            .order_by(MLPipelineRunModel.created_at.asc())
            .with_for_update()
        )
        return result.scalars().first()

    async def _read_active_for_dataset(self, dataset_version: str) -> MLPipelineRunModel | None:
        result = await self._session.execute(
            select(MLPipelineRunModel)
            .where(
                MLPipelineRunModel.dataset_version == dataset_version,
                MLPipelineRunModel.status.in_(ACTIVE_RUN_STATUSES),
            )
            .order_by(MLPipelineRunModel.created_at.asc())
        )
        return result.scalars().first()

    async def _read_one_by_status(self, status: str) -> MLPipelineRunModel | None:
        result = await self._session.execute(
            select(MLPipelineRunModel)
            .where(MLPipelineRunModel.status == status)
            .order_by(MLPipelineRunModel.created_at.asc())
        )
        return result.scalars().first()

    @staticmethod
    def _new_run(
        *,
        status: str,
        dataset_version: str,
        baseline_model_version: str,
        requested_at: datetime,
    ) -> MLPipelineRunModel:
        run_id = uuid4()
        return MLPipelineRunModel(
            id=run_id,
            status=status,
            dataset_version=dataset_version,
            baseline_model_version=baseline_model_version,
            candidate_model_version=candidate_model_version_for_run(run_id),
            created_at=requested_at,
            updated_at=requested_at,
        )
