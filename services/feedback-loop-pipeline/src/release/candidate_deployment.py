from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from loguru import logger

from src.release.model_reload import ModelReloadError
from src.utils.clock import Clock, SystemClock


class CandidateDeploymentRunStore(Protocol):
    async def get(self, run_id: UUID): ...

    async def record_deployment_attempt_failure(
        self,
        *,
        run_id: UUID,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        updated_at,
    ) -> int: ...

    async def mark_deployment_blocked(
        self,
        *,
        run_id: UUID,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        blocked_at,
    ) -> None: ...

    async def reset_deployment_attempts(self, *, run_id: UUID, updated_at) -> None: ...


class CandidateDeploymentTransitionManager(Protocol):
    async def open_candidate_release(self, *, run_id: UUID, trace_id: UUID): ...

    async def cutover_candidate_release(self, *, run_id: UUID, trace_id: UUID): ...


class CandidateModelReloadClient(Protocol):
    async def reload(self, *, trace_id: UUID): ...


@dataclass(frozen=True)
class CandidateDeploymentResult:
    status: str
    run_id: UUID
    attempt_count: int
    reason: str | None = None


class CandidateDeploymentService:
    _FAILED_STAGE = "candidate_deployment"

    def __init__(
        self,
        *,
        run_store: CandidateDeploymentRunStore,
        transition_manager: CandidateDeploymentTransitionManager,
        reload_client: CandidateModelReloadClient,
        max_attempts: int,
        clock: Clock | None = None,
    ) -> None:
        self._run_store = run_store
        self._transition_manager = transition_manager
        self._reload_client = reload_client
        self._max_attempts = max_attempts
        self._clock = clock or SystemClock()

    async def attempt(self, *, run_id: UUID, trace_id: UUID) -> CandidateDeploymentResult:
        run = await self._run_store.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        if run.status == "DEPLOYMENT_BLOCKED":
            return CandidateDeploymentResult("already_blocked", run_id, run.deployment_attempt_count)

        candidate = await self._transition_manager.open_candidate_release(run_id=run_id, trace_id=trace_id)
        reload_result = await self._reload_candidate_model(
            run_id=run_id,
            trace_id=trace_id,
            candidate_model_version=candidate.candidate_model_version,
        )
        if reload_result is not None:
            return reload_result

        cutover = await self._transition_manager.cutover_candidate_release(run_id=run_id, trace_id=trace_id)
        if cutover.status == "cutover":
            await self._run_store.reset_deployment_attempts(run_id=run_id, updated_at=self._clock.now())
        return CandidateDeploymentResult(
            status=cutover.status,
            run_id=run_id,
            attempt_count=run.deployment_attempt_count,
        )

    async def _reload_candidate_model(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
        candidate_model_version: str,
    ) -> CandidateDeploymentResult | None:
        try:
            reload_result = await self._reload_client.reload(trace_id=trace_id)
        except ModelReloadError as exc:
            return await self._record_readiness_failure(run_id, f"reload-models failed: {exc}")
        if candidate_model_version not in reload_result.ready_model_versions:
            return await self._record_readiness_failure(
                run_id,
                f"candidate model is not ready after reload: {candidate_model_version}",
            )
        return None

    async def _record_readiness_failure(self, run_id: UUID, reason: str) -> CandidateDeploymentResult:
        now = self._clock.now()
        attempt_count = await self._run_store.record_deployment_attempt_failure(
            run_id=run_id,
            failed_stage=self._FAILED_STAGE,
            failure_type="ERROR",
            failure_reason=reason,
            updated_at=now,
        )
        logger.bind(run_id=str(run_id), attempt_count=attempt_count).warning(
            "candidate deployment readiness failed: {}", reason
        )
        if attempt_count >= self._max_attempts:
            blocked_reason = (
                f"candidate deployment blocked after {attempt_count} attempts: {reason}"
            )
            await self._run_store.mark_deployment_blocked(
                run_id=run_id,
                failed_stage=self._FAILED_STAGE,
                failure_type="ERROR",
                failure_reason=blocked_reason,
                blocked_at=now,
            )
            return CandidateDeploymentResult("blocked", run_id, attempt_count, blocked_reason)
        return CandidateDeploymentResult("not_ready", run_id, attempt_count, reason)
