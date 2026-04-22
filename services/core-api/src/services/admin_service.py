"""Admin service skeleton.

Scope (foundation): Hold the seam between admin router wiring and the full
admin-control flow owned by the Admin Control Plane branch.

Out of scope: training trigger, rollback orchestration, readiness checks,
role-based authorization. Those land in the admin-control branch.
"""

from typing import Any

from src.infra.broker import BrokerClient


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

    def trigger_rollback(self) -> None:
        raise NotImplementedError(
            "AdminService.trigger_rollback is implemented in the admin-control branch."
        )
