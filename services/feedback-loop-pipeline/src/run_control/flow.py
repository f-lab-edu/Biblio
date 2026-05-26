from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from loguru import logger

from src.evaluation.evaluator import EvaluationResult
from src.training.runner import TrainingInput, TrainingOutput, TrainingRunner
from src.utils.clock import Clock, SystemClock


@dataclass
class RunRecord:
    id: UUID
    trace_id: UUID
    status: str
    dataset_version: str
    dataset_artifact_ref: str
    baseline_model_version: str
    candidate_model_version: str
    failed_stage: str | None = None
    failure_type: str | None = None
    failure_reason: str | None = None


class EvaluatorPort(Protocol):
    async def evaluate(
        self,
        *,
        baseline_model_version: str,
        candidate_model_version: str,
        evaluation_dataset_ref: str,
    ) -> EvaluationResult: ...


class RunStateStorePort(Protocol):
    async def get_running(self, run_id: UUID) -> RunRecord: ...

    async def record_training_output(self, run_id: UUID, output: TrainingOutput) -> None: ...

    async def mark_ready_for_release(self, run_id: UUID, evaluation_id: UUID) -> None: ...

    async def mark_failed(
        self,
        run_id: UUID,
        *,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        evaluation_id: UUID | None = None,
    ) -> None: ...


class EvaluationRecorderPort(Protocol):
    async def save(self, result: EvaluationResult) -> UUID: ...


class HandoffSinkPort(Protocol):
    async def ready_for_release(self, *, run_id: UUID, trace_id: UUID) -> None: ...


class _StageFailure(Exception):
    def __init__(
        self,
        stage: str,
        failure_type: str,
        reason: str,
        evaluation_id: UUID | None = None,
    ) -> None:
        self.stage = stage
        self.failure_type = failure_type
        self.reason = reason
        self.evaluation_id = evaluation_id


class RunFlowService:
    def __init__(
        self,
        *,
        run_state_store: RunStateStorePort,
        training_runner: TrainingRunner,
        evaluator: EvaluatorPort,
        evaluation_recorder: EvaluationRecorderPort,
        handoff_sink: HandoffSinkPort,
        evaluation_dataset_ref: str,
        training_config_ref: str,
        training_config_hash: str,
        clock: Clock | None = None,
    ) -> None:
        self._run_state_store = run_state_store
        self._training_runner = training_runner
        self._evaluator = evaluator
        self._evaluation_recorder = evaluation_recorder
        self._handoff_sink = handoff_sink
        self._evaluation_dataset_ref = evaluation_dataset_ref
        self._training_config_ref = training_config_ref
        self._training_config_hash = training_config_hash
        self._clock = clock or SystemClock()

    async def run(self, run_id: UUID, *, workspace_dir: Path) -> None:
        run = await self._run_state_store.get_running(run_id)
        try:
            training_output = await self._run_training(run, workspace_dir)
            evaluation_result, evaluation_id = await self._run_evaluation(run)
            await self._finalize(run, evaluation_result, evaluation_id)
        except _StageFailure as f:
            await self._run_state_store.mark_failed(
                run.id,
                failed_stage=f.stage,
                failure_type=f.failure_type,
                failure_reason=f.reason,
                evaluation_id=f.evaluation_id,
            )

    async def _run_training(self, run: RunRecord, workspace_dir: Path) -> TrainingOutput:
        try:
            output = await self._training_runner.train(
                TrainingInput(
                    ml_pipeline_run_id=str(run.id),
                    trace_id=str(run.trace_id),
                    dataset_version=run.dataset_version,
                    dataset_artifact_ref=run.dataset_artifact_ref,
                    baseline_model_version=run.baseline_model_version,
                    candidate_model_version=run.candidate_model_version,
                    evaluation_dataset_ref=self._evaluation_dataset_ref,
                    training_config_ref=self._training_config_ref,
                    training_config_hash=self._training_config_hash,
                    started_at=self._clock.now(),
                ),
                workspace_dir=workspace_dir,
            )
        except Exception as exc:
            raise _StageFailure("학습", "ERROR", str(exc)) from exc
        await self._run_state_store.record_training_output(run.id, output)
        return output

    async def _run_evaluation(self, run: RunRecord) -> tuple[EvaluationResult, UUID]:
        try:
            result = await self._evaluator.evaluate(
                baseline_model_version=run.baseline_model_version,
                candidate_model_version=run.candidate_model_version,
                evaluation_dataset_ref=self._evaluation_dataset_ref,
            )
        except Exception as exc:
            raise _StageFailure("평가", "ERROR", f"evaluation_failed: {exc}") from exc
        try:
            evaluation_id = await self._evaluation_recorder.save(result)
        except Exception as exc:
            raise _StageFailure("평가", "ERROR", f"evaluation_result_save_failed: {exc}") from exc
        return result, evaluation_id

    async def _finalize(
        self,
        run: RunRecord,
        evaluation_result: EvaluationResult,
        evaluation_id: UUID,
    ) -> None:
        if evaluation_result.overall_decision != "PASS":
            raise _StageFailure(
                "평가",
                "FAIL",
                evaluation_result.fail_reason or "candidate metrics did not meet baseline",
                evaluation_id=evaluation_id,
            )
        await self._run_state_store.mark_ready_for_release(run.id, evaluation_id)
        try:
            await self._handoff_sink.ready_for_release(run_id=run.id, trace_id=run.trace_id)
        except Exception as exc:
            logger.bind(
                run_id=str(run.id),
                trace_id=str(run.trace_id),
                evaluation_id=str(evaluation_id),
            ).warning("release handoff failed after READY_FOR_RELEASE: {}", exc)
