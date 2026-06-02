"""Admin service.

Owns the admin-control seam between the admin router and downstream control
workers. `trigger_rollback` validates the current `ModelRelease` and publishes a
`ROLLBACK_REQUEST` control message; the rollback worker in feedback-loop-pipeline
performs the actual rollback.

Out of scope: training trigger and readiness checks. Those land in their own
branches.
"""

from typing import Any
from uuid import UUID

from src.infra.broker import BrokerClient, BrokerPublishError, build_control_message
from src.infra.db.model_release_repository import ModelReleaseRepository
from src.middlewares.error_handler import ApiError, ConflictError
from src.models.admin_ops import ModelRelease
from src.schemas.admin_ops import ControlMessageType, ReleaseStatus

ROLLBACK_PUBLISH_MAX_ATTEMPTS = 3


class AdminService:
    def __init__(
        self,
        *,
        db_session_factory: Any,
        broker_client: BrokerClient | None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._broker_client = broker_client

    def trigger_training(self) -> None:
        raise NotImplementedError(
            "AdminService.trigger_training is implemented in the admin-control branch."
        )

    async def trigger_rollback(
        self,
        *,
        trace_id: UUID,
        rollback_queue_name: str,
    ) -> dict[str, bool]:
        async with self._db_session_factory() as session:
            repository = ModelReleaseRepository(session)
            release = await repository.get_current()
            self._ensure_rollbackable(release)
            if not await repository.has_previous_stable_snapshot():
                raise ConflictError("No previous stable snapshot is available to roll back.")
            message = build_control_message(
                ControlMessageType.ROLLBACK_REQUEST.value,
                trace_id=trace_id,
                queue_name=rollback_queue_name,
                expected_active_model_version=release.active_model_version,
                expected_switched_at=release.switched_at,
            )
        await self._publish(message)
        return {"rollback_requested": True}

    @staticmethod
    def _ensure_rollbackable(release: ModelRelease | None) -> None:
        if release is None:
            raise ConflictError("No model release is available to roll back.")
        if release.release_status != ReleaseStatus.STABLE.value:
            raise ConflictError("Model release must be STABLE to roll back.")
        if release.switched_at is None:
            raise ConflictError("Model release switch time is not available.")

    async def _publish(self, message: Any) -> None:
        if self._broker_client is None:
            raise ApiError("Message broker is not configured.")
        try:
            await self._broker_client.publish_with_retry(
                message,
                max_attempts=ROLLBACK_PUBLISH_MAX_ATTEMPTS,
            )
        except BrokerPublishError as exc:
            raise ApiError("Message broker publish failed after retries.") from exc
