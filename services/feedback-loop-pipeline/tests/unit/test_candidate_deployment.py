from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from src.release.candidate_deployment import CandidateDeploymentService
from src.release.transition import CandidateReleaseResult, CutoverResult


@dataclass
class _Run:
    id: UUID
    status: str
    deployment_attempt_count: int = 0


@dataclass(frozen=True)
class _ReloadResult:
    ready_model_versions: frozenset[str]


class _RunStore:
    def __init__(self, run: _Run) -> None:
        self.run = run
        self.failures: list[str] = []
        self.blocked: list[str] = []
        self.reset_calls = 0

    async def get(self, run_id: UUID):
        return self.run if run_id == self.run.id else None

    async def record_deployment_attempt_failure(
        self,
        *,
        run_id: UUID,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        updated_at: datetime,
    ) -> int:
        self.run.deployment_attempt_count += 1
        self.failures.append(failure_reason)
        return self.run.deployment_attempt_count

    async def mark_deployment_blocked(
        self,
        *,
        run_id: UUID,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        blocked_at: datetime,
    ) -> None:
        self.run.status = "DEPLOYMENT_BLOCKED"
        self.blocked.append(failure_reason)

    async def reset_deployment_attempts(
        self,
        *,
        run_id: UUID,
        updated_at: datetime,
    ) -> None:
        self.run.deployment_attempt_count = 0
        self.reset_calls += 1


class _TransitionManager:
    def __init__(self) -> None:
        self.open_calls = 0
        self.cutover_calls = 0

    async def open_candidate_release(self, *, run_id: UUID, trace_id: UUID):
        self.open_calls += 1
        return CandidateReleaseResult(
            status="opened",
            run_id=run_id,
            candidate_model_version="model-v2",
            candidate_index_name="candidate-index-model-v2",
        )

    async def cutover_candidate_release(self, *, run_id: UUID, trace_id: UUID):
        self.cutover_calls += 1
        return CutoverResult(status="cutover", run_id=run_id)


class _ReloadClient:
    def __init__(self, ready_versions: frozenset[str]) -> None:
        self.ready_versions = ready_versions
        self.calls = 0

    async def reload(self, *, trace_id: UUID):
        self.calls += 1
        return _ReloadResult(ready_model_versions=self.ready_versions)


class _Clock:
    def now(self) -> datetime:
        return datetime(2026, 6, 2, 12, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_deployment_reloads_candidate_and_cutovers_when_ready() -> None:
    run = _Run(id=uuid4(), status="READY_FOR_RELEASE")
    run_store = _RunStore(run)
    transition = _TransitionManager()
    reload_client = _ReloadClient(frozenset({"model-v2"}))

    result = await CandidateDeploymentService(
        run_store=run_store,
        transition_manager=transition,
        reload_client=reload_client,
        max_attempts=5,
        clock=_Clock(),
    ).attempt(run_id=run.id, trace_id=uuid4())

    assert result.status == "cutover"
    assert transition.open_calls == 1
    assert reload_client.calls == 1
    assert transition.cutover_calls == 1
    assert run_store.reset_calls == 1


@pytest.mark.asyncio
async def test_deployment_blocks_after_fifth_candidate_readiness_failure() -> None:
    run = _Run(id=uuid4(), status="READY_FOR_RELEASE", deployment_attempt_count=4)
    run_store = _RunStore(run)
    transition = _TransitionManager()
    reload_client = _ReloadClient(frozenset({"model-v1"}))

    result = await CandidateDeploymentService(
        run_store=run_store,
        transition_manager=transition,
        reload_client=reload_client,
        max_attempts=5,
        clock=_Clock(),
    ).attempt(run_id=run.id, trace_id=uuid4())

    assert result.status == "blocked"
    assert run.status == "DEPLOYMENT_BLOCKED"
    assert transition.open_calls == 1
    assert reload_client.calls == 1
    assert transition.cutover_calls == 0
    assert len(run_store.failures) == 1
    assert len(run_store.blocked) == 1
