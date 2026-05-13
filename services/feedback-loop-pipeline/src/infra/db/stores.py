from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from src.evaluation.evaluator import EvaluationResult
from src.infra.db.models import (
    ChunkModel,
    MLPipelineRunModel,
    ModelEvaluationModel,
    ModelReleaseModel,
    ProjectModel,
    VectorIndexEntryModel,
    VideoModel,
)


class ModelReleaseStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_current(self) -> ModelReleaseModel | None:
        result = await self._session.execute(
            select(ModelReleaseModel).where(ModelReleaseModel.singleton_key == 1)
        )
        return result.scalar_one_or_none()

    async def try_open_candidate_release(
        self,
        *,
        candidate_model_version: str,
        candidate_index_name: str,
        updated_at: datetime,
    ) -> ModelReleaseModel | None:
        result = await self._session.execute(
            update(ModelReleaseModel)
            .where(
                ModelReleaseModel.singleton_key == 1,
                ModelReleaseModel.release_status == "STABLE",
            )
            .values(
                release_status="CANDIDATE_REINDEXING",
                candidate_model_version=candidate_model_version,
                candidate_index_name=candidate_index_name,
                candidate_opened_at=updated_at,
                candidate_ready_at=None,
                updated_at=updated_at,
            )
            .execution_options(synchronize_session="fetch")
        )
        if result.rowcount != 1:
            return None
        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")
        await self._session.refresh(release)
        return release

    async def mark_candidate_ready(
        self,
        *,
        ready_at: datetime,
    ) -> None:
        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")
        release.candidate_ready_at = ready_at
        release.updated_at = ready_at
        await self._session.flush()

    async def complete_candidate_cutover(
        self,
        *,
        switched_at: datetime,
    ) -> ModelReleaseModel:
        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")
        if release.candidate_model_version is None or release.candidate_index_name is None:
            raise ValueError("candidate release fields are required for cutover")

        previous_active_model_version = release.active_model_version
        previous_active_index_name = release.active_index_name

        release.rollback_snapshot_active_model_version = previous_active_model_version
        release.rollback_snapshot_active_index_name = previous_active_index_name
        release.rollback_snapshot_captured_at = switched_at
        release.previous_model_version = previous_active_model_version
        release.previous_index_name = previous_active_index_name
        release.active_model_version = release.candidate_model_version
        release.active_index_name = release.candidate_index_name
        release.candidate_model_version = None
        release.candidate_index_name = None
        release.candidate_opened_at = None
        release.candidate_ready_at = None
        release.release_status = "STABLE"
        release.switched_at = switched_at
        release.updated_at = switched_at
        await self._session.flush()
        return release

    async def mark_rollback_preparing(self, *, updated_at: datetime) -> ModelReleaseModel:
        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")
        release.release_status = "ROLLBACK_PREPARING"
        release.updated_at = updated_at
        await self._session.flush()
        return release

    async def complete_rollback_restore(self, *, restored_at: datetime) -> ModelReleaseModel:
        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")
        if (
            release.rollback_snapshot_active_model_version is None
            or release.rollback_snapshot_active_index_name is None
        ):
            raise ValueError("rollback snapshot active model/index are required")

        release.active_model_version = release.rollback_snapshot_active_model_version
        release.active_index_name = release.rollback_snapshot_active_index_name
        release.previous_model_version = None
        release.previous_index_name = None
        release.candidate_model_version = None
        release.candidate_index_name = None
        release.candidate_opened_at = None
        release.candidate_ready_at = None
        release.release_status = "STABLE"
        release.switched_at = restored_at
        release.updated_at = restored_at
        await self._session.flush()
        return release


class ModelEvaluationStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_completed_evaluation(
        self,
        result: EvaluationResult,
        *,
        created_at: datetime,
    ) -> ModelEvaluationModel:
        evaluation = ModelEvaluationModel(
            candidate_model_version=result.candidate_model_version,
            baseline_model_version=result.baseline_model_version,
            evaluation_dataset_ref=result.evaluation_dataset_ref,
            sample_count=result.sample_count,
            status="COMPLETED",
            quality_metrics=result.quality_metrics,
            pass_criteria=result.pass_criteria,
            overall_decision=result.overall_decision,
            fail_reason=result.fail_reason,
            created_at=created_at,
        )
        self._session.add(evaluation)
        await self._session.flush()
        return evaluation


class MLPipelineRunStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, run_id: UUID) -> MLPipelineRunModel | None:
        result = await self._session.execute(
            select(MLPipelineRunModel).where(MLPipelineRunModel.id == run_id)
        )
        return result.scalar_one_or_none()

    async def create_running_run(
        self,
        *,
        dataset_version: str,
        baseline_model_version: str,
        candidate_model_version: str,
        created_at: datetime,
    ) -> MLPipelineRunModel:
        run = MLPipelineRunModel(
            status="RUNNING",
            dataset_version=dataset_version,
            baseline_model_version=baseline_model_version,
            candidate_model_version=candidate_model_version,
            created_at=created_at,
            updated_at=created_at,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def record_evaluation_ready_for_release(
        self,
        *,
        run_id: UUID,
        evaluation_id: UUID,
        updated_at: datetime,
    ) -> None:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.status = "READY_FOR_RELEASE"
        run.evaluation_id = evaluation_id
        run.updated_at = updated_at
        await self._session.flush()

    async def mark_failed(
        self,
        *,
        run_id: UUID,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        evaluation_id: UUID | None = None,
        updated_at: datetime,
    ) -> None:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.status = "FAILED"
        run.failed_stage = failed_stage
        run.failure_type = failure_type
        run.failure_reason = failure_reason
        if evaluation_id is not None:
            run.evaluation_id = evaluation_id
        run.updated_at = updated_at
        await self._session.flush()

    async def record_candidate_index(
        self,
        *,
        run_id: UUID,
        candidate_index_name: str,
        updated_at: datetime,
    ) -> None:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.candidate_index_name = candidate_index_name
        run.updated_at = updated_at
        await self._session.flush()

    async def record_cutover_time(
        self,
        *,
        run_id: UUID,
        cutover_time: datetime,
        updated_at: datetime,
    ) -> None:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.cutover_time = cutover_time
        run.updated_at = updated_at
        await self._session.flush()


class ProjectRollbackStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def exclude_projects_for_problem_model(
        self,
        *,
        problem_model_version: str,
        updated_at: datetime,
    ) -> int:
        project_ids = await self._affected_project_ids(problem_model_version)
        if not project_ids:
            return 0
        await self._session.execute(
            update(ProjectModel)
            .where(ProjectModel.id.in_(project_ids))
            .values(search_serving_state="ROLLBACK_EXCLUDED", updated_at=updated_at)
        )
        await self._session.flush()
        return len(project_ids)

    async def _affected_project_ids(self, problem_model_version: str) -> list[UUID]:
        result = await self._session.execute(
            select(VideoModel.project_id)
            .join(ChunkModel, ChunkModel.video_id == VideoModel.id)
            .where(
                VideoModel.project_id.is_not(None),
                ChunkModel.embedding_model_version == problem_model_version,
            )
            .distinct()
        )
        return list(result.scalars().all())

    async def reenter_restored_projects(
        self,
        *,
        active_model_version: str,
        active_index_name: str,
        updated_at: datetime,
    ) -> int:
        result = await self._session.execute(
            select(ProjectModel.id).where(ProjectModel.search_serving_state == "ROLLBACK_EXCLUDED")
        )
        project_ids = list(result.scalars().all())
        reenterable_project_ids = [
            project_id
            for project_id in project_ids
            if await self._is_project_restored(
                project_id,
                active_model_version=active_model_version,
                active_index_name=active_index_name,
            )
        ]
        if not reenterable_project_ids:
            return 0
        await self._session.execute(
            update(ProjectModel)
            .where(ProjectModel.id.in_(reenterable_project_ids))
            .values(search_serving_state="SERVABLE", updated_at=updated_at)
        )
        await self._session.flush()
        return len(reenterable_project_ids)

    async def _is_project_restored(
        self,
        project_id: UUID,
        *,
        active_model_version: str,
        active_index_name: str,
    ) -> bool:
        ready_video_ids = await self._ready_video_ids(project_id)
        if not ready_video_ids:
            return False
        for video_id in ready_video_ids:
            if not await self._is_video_restored(
                video_id,
                active_model_version=active_model_version,
                active_index_name=active_index_name,
            ):
                return False
        return True

    async def _is_video_restored(
        self,
        video_id: UUID,
        *,
        active_model_version: str,
        active_index_name: str,
    ) -> bool:
        chunk_ids = await self._video_chunk_ids(video_id)
        if not chunk_ids:
            return False
        if await self._has_non_restored_chunks(
            video_id,
            active_model_version=active_model_version,
        ):
            return False
        return not await self._has_missing_active_vectors(
            chunk_ids,
            active_model_version=active_model_version,
            active_index_name=active_index_name,
        )

    async def _video_chunk_ids(self, video_id: UUID) -> list[UUID]:
        result = await self._session.execute(
            select(ChunkModel.id).where(ChunkModel.video_id == video_id)
        )
        return list(result.scalars().all())

    async def _has_non_restored_chunks(self, video_id: UUID, *, active_model_version: str) -> bool:
        result = await self._session.execute(
            select(ChunkModel.id).where(
                ChunkModel.video_id == video_id,
                ChunkModel.embedding_model_version != active_model_version,
            )
        )
        return result.scalars().first() is not None

    async def _ready_video_ids(self, project_id: UUID) -> list[UUID]:
        result = await self._session.execute(
            select(VideoModel.id).where(VideoModel.project_id == project_id, VideoModel.status == "READY")
        )
        return list(result.scalars().all())

    async def _has_missing_active_vectors(
        self,
        chunk_ids: list[UUID],
        *,
        active_model_version: str,
        active_index_name: str,
    ) -> bool:
        result = await self._session.execute(
            select(VectorIndexEntryModel.chunk_id).where(
                VectorIndexEntryModel.chunk_id.in_(chunk_ids),
                VectorIndexEntryModel.index_name == active_index_name,
                VectorIndexEntryModel.embedding_model_version == active_model_version,
            )
        )
        reflected_chunk_ids = set(result.scalars().all())
        return any(chunk_id not in reflected_chunk_ids for chunk_id in chunk_ids)

    async def restored_reembedding_video_ids(
        self,
        *,
        active_model_version: str,
        active_index_name: str,
    ) -> list[UUID]:
        result = await self._session.execute(
            select(VideoModel.id, VideoModel.project_id)
            .join(ProjectModel, ProjectModel.id == VideoModel.project_id)
            .where(
                ProjectModel.search_serving_state == "ROLLBACK_EXCLUDED",
                VideoModel.status == "READY",
            )
            .order_by(VideoModel.updated_at.asc(), VideoModel.id.asc())
        )
        video_rows = list(result.all())
        video_ids: list[UUID] = []
        for row in video_rows:
            if not await self._is_video_restored(
                row.id,
                active_model_version=active_model_version,
                active_index_name=active_index_name,
            ):
                video_ids.append(row.id)
        return video_ids


class VectorIndexProjectionReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_missing_candidate_chunk_ids(
        self,
        *,
        active_index_name: str,
        active_model_version: str,
        candidate_index_name: str,
        candidate_model_version: str,
        candidate_opened_at: datetime,
        cutover_time: datetime,
        limit: int = 100,
    ) -> list[UUID]:
        active_entry = aliased(VectorIndexEntryModel)
        candidate_entry = aliased(VectorIndexEntryModel)
        candidate_row_exists = (
            select(candidate_entry.chunk_id)
            .where(
                candidate_entry.chunk_id == active_entry.chunk_id,
                candidate_entry.index_name == candidate_index_name,
                candidate_entry.embedding_model_version == candidate_model_version,
            )
            .exists()
        )
        active_rows = (
            select(active_entry.chunk_id)
            .where(
                active_entry.index_name == active_index_name,
                active_entry.embedding_model_version == active_model_version,
                active_entry.created_at >= candidate_opened_at,
                active_entry.created_at <= cutover_time,
                ~candidate_row_exists,
            )
            .order_by(active_entry.created_at.asc())
            .limit(limit)
        )
        result = await self._session.execute(active_rows)
        return list(result.scalars().all())
