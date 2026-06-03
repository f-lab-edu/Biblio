from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from loguru import logger

from src.infra.db.stores import ModelReleaseStore, ProjectRollbackStore
from src.observability.metrics import MetricsRecorder, NoopMetricsRecorder
from src.release.serving_reload import (
    NoopReleaseChangeCommitter,
    NoopServingTargetReloader,
    ReleaseChangeCommitter,
    ServingTargetReloader,
)
from src.utils.clock import Clock, SystemClock


class RollbackRequestMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_type: Literal["ROLLBACK_REQUEST"]
    payload_version: str = Field(..., pattern=r"^v\d+$")
    trace_id: UUID
    attempt: int = Field(..., ge=1)
    issued_at: datetime
    expected_active_model_version: str
    expected_switched_at: datetime


class RollbackTargetReadinessPort(Protocol):
    async def is_ready(self, *, model_version: str) -> bool: ...


class SnapshotIndexRestorePort(Protocol):
    async def restore_snapshot(self, *, index_name: str) -> bool: ...


class AlwaysReadyRollbackTarget:
    async def is_ready(self, *, model_version: str) -> bool:
        # Async to satisfy the readiness port in local tests and wiring.
        _ = model_version
        return True


class ImmediateIndexRestore:
    async def restore_snapshot(self, *, index_name: str) -> bool:
        # Async to satisfy the restore port in local tests and wiring.
        _ = index_name
        return True


@dataclass(frozen=True)
class RollbackResult:
    status: str
    affected_project_count: int = 0


class UnsupportedPayloadVersionError(ValueError):
    pass


class RollbackTransitionManager:
    def __init__(
        self,
        *,
        release_store: ModelReleaseStore,
        project_store: ProjectRollbackStore,
        target_readiness: RollbackTargetReadinessPort,
        index_restore: SnapshotIndexRestorePort,
        release_change_committer: ReleaseChangeCommitter | None = None,
        serving_target_reloader: ServingTargetReloader | None = None,
        clock: Clock | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self._release_store = release_store
        self._project_store = project_store
        self._target_readiness = target_readiness
        self._index_restore = index_restore
        self._release_change_committer = (
            release_change_committer or NoopReleaseChangeCommitter()
        )
        self._serving_target_reloader = (
            serving_target_reloader or NoopServingTargetReloader()
        )
        self._clock = clock or SystemClock()
        self._metrics = metrics or NoopMetricsRecorder()

    async def handle_request(
        self,
        payload: RollbackRequestMessage | dict[str, object],
    ) -> RollbackResult:
        message = (
            payload
            if isinstance(payload, RollbackRequestMessage)
            else RollbackRequestMessage.model_validate(payload)
        )
        if message.payload_version != "v1":
            raise UnsupportedPayloadVersionError(message.payload_version)

        release = await self._release_store.get_current()
        if release is None:
            return self._result(message, "missing_release")
        target = await self._release_store.get_rollback_target()
        if target is None:
            return self._result(message, "missing_snapshot")
        snapshot_model, snapshot_index = target
        if release.release_status == "STABLE" and release.active_model_version == snapshot_model:
            return self._result(message, "already_restored")
        if release.release_status == "ROLLBACK_PREPARING":
            return await self._continue_restore(
                message,
                snapshot_model=snapshot_model,
                snapshot_index=snapshot_index,
            )
        if release.release_status != "STABLE":
            return self._result(message, "invalid_state")
        if release.active_model_version != message.expected_active_model_version or not _same_datetime(
            release.switched_at,
            message.expected_switched_at,
        ):
            return self._result(message, "stale_request")

        now = self._clock.now()
        affected_count = await self._project_store.exclude_projects_for_problem_model(
            problem_model_version=release.active_model_version,
            updated_at=now,
        )
        await self._release_store.mark_rollback_preparing(updated_at=now)

        return await self._continue_restore(
            message,
            snapshot_model=snapshot_model,
            snapshot_index=snapshot_index,
            affected_project_count=affected_count,
            restored_at=now,
        )

    async def _continue_restore(
        self,
        message: RollbackRequestMessage,
        *,
        snapshot_model: str,
        snapshot_index: str,
        affected_project_count: int = 0,
        restored_at: datetime | None = None,
    ) -> RollbackResult:
        if not await self._target_readiness.is_ready(model_version=snapshot_model):
            return self._result(message, "blocked_not_ready", affected_project_count=affected_project_count)
        if not await self._index_restore.restore_snapshot(index_name=snapshot_index):
            return self._result(message, "blocked_index_restore", affected_project_count=affected_project_count)

        await self._release_store.complete_rollback_restore(restored_at=restored_at or self._clock.now())
        await self._release_change_committer.commit()
        await self._serving_target_reloader.reload(trace_id=message.trace_id)
        return self._result(message, "restored", affected_project_count=affected_project_count)

    def _result(
        self,
        message: RollbackRequestMessage,
        status: str,
        *,
        affected_project_count: int = 0,
    ) -> RollbackResult:
        self._metrics.increment(
            "feedback_loop.rollback_request_total",
            tags={"status": status},
        )
        if affected_project_count > 0:
            self._metrics.increment(
                "feedback_loop.rollback_affected_project_total",
                value=affected_project_count,
            )
        logger.bind(
            trace_id=str(message.trace_id),
            rollback_status=status,
            affected_project_count=affected_project_count,
            expected_active_model_version=message.expected_active_model_version,
        ).info("rollback.request handled")
        return RollbackResult(status=status, affected_project_count=affected_project_count)


def _same_datetime(left: datetime | None, right: datetime) -> bool:
    if left is None:
        return False
    if left == right:
        return True
    return left.replace(tzinfo=None) == right.replace(tzinfo=None)
