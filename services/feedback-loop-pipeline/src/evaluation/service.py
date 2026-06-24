from __future__ import annotations

from pathlib import Path

from src.evaluation.dataset_loader import EvaluationDatasetLoader
from src.evaluation.evaluator import EvaluationResult, OfflineEvaluator
from src.evaluation.model_artifacts import LocalScoringModelArtifactLoader
from src.utils.clock import Clock, SystemClock


class OfflineEvaluationService:
    def __init__(
        self,
        *,
        dataset_loader: EvaluationDatasetLoader,
        evaluator: OfflineEvaluator,
        workspace_dir: Path,
        model_artifact_loader: LocalScoringModelArtifactLoader | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._dataset_loader = dataset_loader
        self._evaluator = evaluator
        self._workspace_dir = workspace_dir
        self._model_artifact_loader = model_artifact_loader
        self._clock = clock or SystemClock()

    async def evaluate(
        self,
        *,
        baseline_model_version: str,
        candidate_model_version: str,
        evaluation_dataset_ref: str,
    ) -> EvaluationResult:
        dataset = await self._dataset_loader.load(
            evaluation_dataset_ref,
            workspace_dir=self._workspace_dir,
        )
        if self._model_artifact_loader is not None:
            baseline_artifact = await self._model_artifact_loader.load(
                baseline_model_version,
                workspace_dir=self._workspace_dir,
            )
            candidate_artifact = await self._model_artifact_loader.load(
                candidate_model_version,
                workspace_dir=self._workspace_dir,
            )
            self._evaluator.register_model_artifact(baseline_model_version, baseline_artifact)
            self._evaluator.register_model_artifact(candidate_model_version, candidate_artifact)
        return self._evaluator.evaluate(
            dataset,
            baseline_model_version=baseline_model_version,
            candidate_model_version=candidate_model_version,
            evaluated_at=self._clock.now(),
        )
