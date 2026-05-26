from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.dataset.manifest import DatasetManifestSelector
from src.infra.db.models import ModelReleaseModel
from src.infra.db.stores import ModelReleaseStore
from src.run_control.db_slots import DbRunSlotDecision, DbRunSlotStore
from src.run_control.db_state import DbRunStateStore
from src.run_control.flow import (
    EvaluationRecorderPort,
    EvaluatorPort,
    HandoffSinkPort,
    RunFlowService,
)
from src.training.runner import TrainingRunner
from src.utils.clock import Clock, SystemClock


class TrainingRequestMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_type: Literal["TRAINING_REQUEST"]
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    issued_at: datetime


@dataclass(frozen=True)
class TrainingRequestResult:
    run_id: UUID | None
    status: str
    created: bool
    executed: bool


class RunExecutorPort(Protocol):
    async def execute(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
    ) -> None: ...


class UnsupportedPayloadVersionError(ValueError):
    pass


class MissingModelReleaseError(RuntimeError):
    pass


class TrainingRequestHandler:
    def __init__(
        self,
        *,
        manifest_selector: DatasetManifestSelector,
        dataset_artifact_prefix: str,
        run_slot_store: DbRunSlotStore,
        model_release_store: ModelReleaseStore,
        run_executor: RunExecutorPort,
        workspace_dir: Path,
        clock: Clock | None = None,
    ) -> None:
        self._manifest_selector = manifest_selector
        self._dataset_artifact_prefix = dataset_artifact_prefix
        self._run_slot_store = run_slot_store
        self._model_release_store = model_release_store
        self._run_executor = run_executor
        self._workspace_dir = workspace_dir
        self._clock = clock or SystemClock()

    async def handle(self, payload: dict[str, object] | TrainingRequestMessage) -> TrainingRequestResult:
        message = (
            payload
            if isinstance(payload, TrainingRequestMessage)
            else TrainingRequestMessage.model_validate(payload)
        )
        if message.payload_version != "v1":
            raise UnsupportedPayloadVersionError(message.payload_version)

        manifest = await self._manifest_selector.select_latest_eligible(
            self._dataset_artifact_prefix,
            workspace_dir=self._workspace_dir,
        )
        if manifest is None:
            return TrainingRequestResult(
                run_id=None,
                status="NO_ELIGIBLE_DATASET",
                created=False,
                executed=False,
            )

        release = await self._require_current_release()
        decision = await self._run_slot_store.request_training_run(
            dataset_version=manifest.dataset_version,
            baseline_model_version=release.active_model_version,
            requested_at=self._clock.now(),
        )
        if decision.should_execute_now:
            await self._run_executor.execute(
                run_id=decision.run.id,
                trace_id=message.trace_id,
            )
        return _result_from_decision(decision)

    async def _require_current_release(self) -> ModelReleaseModel:
        release = await self._model_release_store.get_current()
        if release is None:
            raise MissingModelReleaseError("current ModelRelease row is required")
        return release


class RunFlowExecutor:
    def __init__(
        self,
        *,
        session: AsyncSession,
        dataset_artifact_uri_prefix: str,
        training_runner: TrainingRunner,
        evaluator: EvaluatorPort,
        evaluation_recorder: EvaluationRecorderPort,
        handoff_sink: HandoffSinkPort,
        evaluation_dataset_ref: str,
        training_config_ref: str,
        training_config_hash: str,
        workspace_dir: Path,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._dataset_artifact_uri_prefix = dataset_artifact_uri_prefix
        self._training_runner = training_runner
        self._evaluator = evaluator
        self._evaluation_recorder = evaluation_recorder
        self._handoff_sink = handoff_sink
        self._evaluation_dataset_ref = evaluation_dataset_ref
        self._training_config_ref = training_config_ref
        self._training_config_hash = training_config_hash
        self._workspace_dir = workspace_dir
        self._clock = clock or SystemClock()

    async def execute(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
    ) -> None:
        run_state_store = DbRunStateStore(
            session=self._session,
            trace_id=trace_id,
            dataset_artifact_prefix=self._dataset_artifact_uri_prefix,
            clock=self._clock,
        )
        await RunFlowService(
            run_state_store=run_state_store,
            training_runner=self._training_runner,
            evaluator=self._evaluator,
            evaluation_recorder=self._evaluation_recorder,
            handoff_sink=self._handoff_sink,
            evaluation_dataset_ref=self._evaluation_dataset_ref,
            training_config_ref=self._training_config_ref,
            training_config_hash=self._training_config_hash,
            clock=self._clock,
        ).run(run_id, workspace_dir=self._workspace_dir)


def _result_from_decision(decision: DbRunSlotDecision) -> TrainingRequestResult:
    return TrainingRequestResult(
        run_id=decision.run.id,
        status=decision.run.status,
        created=decision.created,
        executed=decision.should_execute_now,
    )
