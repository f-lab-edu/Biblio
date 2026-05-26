from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infra.db.stores import MLPipelineRunStore
from src.run_control.flow import RunRecord
from src.training.runner import TrainingOutput
from src.utils.clock import Clock, SystemClock


class DbRunStateStore:
    def __init__(
        self,
        *,
        session: AsyncSession,
        trace_id: UUID,
        dataset_artifact_prefix: str,
        clock: Clock | None = None,
    ) -> None:
        self._run_store = MLPipelineRunStore(session)
        self._trace_id = trace_id
        self._dataset_artifact_prefix = dataset_artifact_prefix.rstrip("/")
        self._clock = clock or SystemClock()

    async def get_running(self, run_id: UUID) -> RunRecord:
        run = await self._run_store.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        if run.status != "RUNNING":
            raise ValueError(f"MLPipelineRun is not RUNNING: {run_id}")
        if run.candidate_model_version is None:
            raise ValueError(f"MLPipelineRun has no candidate model version: {run_id}")
        return RunRecord(
            id=run.id,
            trace_id=self._trace_id,
            status=run.status,
            dataset_version=run.dataset_version,
            dataset_artifact_ref=self._dataset_rows_uri(run.dataset_version),
            baseline_model_version=run.baseline_model_version,
            candidate_model_version=run.candidate_model_version,
            failed_stage=run.failed_stage,
            failure_type=run.failure_type,
            failure_reason=run.failure_reason,
        )

    async def record_training_output(self, run_id: UUID, output: TrainingOutput) -> None:
        # Async to satisfy RunStateStorePort. The current foundation schema has
        # no training artifact columns, so the artifact itself remains the SOT.
        _ = (run_id, output)

    async def mark_ready_for_release(self, run_id: UUID, evaluation_id: UUID) -> None:
        await self._run_store.record_evaluation_ready_for_release(
            run_id=run_id,
            evaluation_id=evaluation_id,
            updated_at=self._clock.now(),
        )

    async def mark_failed(
        self,
        run_id: UUID,
        *,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        evaluation_id: UUID | None = None,
    ) -> None:
        await self._run_store.mark_failed(
            run_id=run_id,
            failed_stage=failed_stage,
            failure_type=failure_type,
            failure_reason=failure_reason,
            evaluation_id=evaluation_id,
            updated_at=self._clock.now(),
        )

    def _dataset_rows_uri(self, dataset_version: str) -> str:
        return f"{self._dataset_artifact_prefix}/{dataset_version}/train.jsonl"
