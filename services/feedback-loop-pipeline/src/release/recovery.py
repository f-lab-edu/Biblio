from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from src.infra.db.stores import ProjectRollbackStore
from src.observability.metrics import MetricsRecorder, NoopMetricsRecorder


class ReembeddingRequestSink(Protocol):
    async def request_reembedding(
        self,
        *,
        video_id: UUID,
        target_model_version: str,
        target_index_name: str,
    ) -> None: ...


@dataclass(frozen=True)
class RecoveryDispatchResult:
    requested_video_count: int


class RollbackRecoveryService:
    def __init__(
        self,
        *,
        project_store: ProjectRollbackStore,
        reembedding_sink: ReembeddingRequestSink,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self._project_store = project_store
        self._reembedding_sink = reembedding_sink
        self._metrics = metrics or NoopMetricsRecorder()

    async def dispatch_restored_reembedding(
        self,
        *,
        active_model_version: str,
        active_index_name: str,
    ) -> RecoveryDispatchResult:
        video_ids = await self._project_store.restored_reembedding_video_ids(
            active_model_version=active_model_version,
            active_index_name=active_index_name,
        )
        for video_id in video_ids:
            await self._reembedding_sink.request_reembedding(
                video_id=video_id,
                target_model_version=active_model_version,
                target_index_name=active_index_name,
            )
        self._metrics.increment(
            "feedback_loop.rollback_reembedding_requested_total",
            value=len(video_ids),
        )
        return RecoveryDispatchResult(requested_video_count=len(video_ids))
