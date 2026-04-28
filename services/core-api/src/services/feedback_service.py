"""Feedback service skeleton.

Scope (foundation): Hold the seam between feedback router wiring and the full
ingestion/publish flow owned by the Feedback Ingestion Pipeline branch.

Out of scope: raw Object Storage sink, broker publish, validation against
`SearchResponseSnapshot`. Those land in the FIP branch.
"""

from typing import Any

from src.infra.broker import BrokerClient
from src.schemas.feedback_dto import FeedbackRequest


class FeedbackService:
    def __init__(
        self,
        *,
        db_session_factory: Any,
        broker_client: BrokerClient | None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._broker_client = broker_client

    def record_request(self, request: FeedbackRequest) -> None:
        """Record a feedback request.

        Placeholder for the full ingestion/publish flow implemented in the
        Feedback Ingestion Pipeline branch.
        """
        _ = request
        raise NotImplementedError(
            "FeedbackService.record_request is implemented in the feedback-ingestion branch."
        )
