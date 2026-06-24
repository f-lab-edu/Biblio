from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.evaluation.artifacts import EvaluationDetailArtifactWriter
from src.evaluation.evaluator import EvaluationResult
from src.infra.db.stores import ModelEvaluationStore
from src.utils.clock import Clock, SystemClock


class EvaluationRecorder:
    def __init__(
        self,
        *,
        session: AsyncSession,
        detail_writer: EvaluationDetailArtifactWriter,
        workspace_dir: Path,
        clock: Clock | None = None,
    ) -> None:
        self._store = ModelEvaluationStore(session)
        self._detail_writer = detail_writer
        self._workspace_dir = workspace_dir
        self._clock = clock or SystemClock()

    async def save(self, result: EvaluationResult) -> UUID:
        evaluation = await self._store.create_completed_evaluation(
            result,
            created_at=self._clock.now(),
        )
        await self._detail_writer.write_details(
            evaluation.id,
            result,
            workspace_dir=self._workspace_dir,
        )
        return evaluation.id
