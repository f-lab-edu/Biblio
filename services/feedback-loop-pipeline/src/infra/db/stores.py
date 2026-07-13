from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select, update
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


class DbChunkTextSnapshot:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def text_by_chunk_id(self, chunk_ids: set[str]) -> dict[str, str]:
        parsed_chunk_ids = _parse_uuid_set(chunk_ids)
        if not parsed_chunk_ids:
            return {}
        result = await self._session.execute(
            select(ChunkModel.id, ChunkModel.text).where(ChunkModel.id.in_(parsed_chunk_ids))
        )
        return {str(row.id): row.text for row in result}

    async def random_negative_pool(
        self,
        project_ids: set[str],
        excluded_chunk_ids: dict[str, set[str]],
        limit_per_project: int,
    ) -> dict[str, dict[str, str]]:
        if limit_per_project < 1:
            return {}
        
        parsed_project_ids = _parse_uuid_set(project_ids)
        if not parsed_project_ids:
            return {}
        
        excluded_ids = {
            parsed_id
            for ids in excluded_chunk_ids.values()
            for parsed_id in _parse_uuid_set(ids)
        }

        row_number = func.row_number().over(
            partition_by=ProjectModel.id,
            order_by=func.random(),
        ).label("row_number")

        ranked = (
            select(
                ProjectModel.id.label("project_id"),
                ChunkModel.id.label("chunk_id"),
                ChunkModel.text.label("chunk_text"),
                row_number,
            )
            .join(VideoModel, ChunkModel.video_id == VideoModel.id)
            .join(ProjectModel, VideoModel.project_id == ProjectModel.id)
            .where(
                ProjectModel.id.in_(parsed_project_ids),
                ProjectModel.search_serving_state == "SERVABLE",
                VideoModel.user_id == ProjectModel.user_id,
                VideoModel.status == "READY",
            )
        )

        if excluded_ids:
            ranked = ranked.where(ChunkModel.id.not_in(excluded_ids))
        ranked_subquery = ranked.subquery()

        result = await self._session.execute(
            select(
                ranked_subquery.c.project_id,
                ranked_subquery.c.chunk_id,
                ranked_subquery.c.chunk_text,
            ).where(ranked_subquery.c.row_number <= limit_per_project)
        )

        pools: dict[str, dict[str, str]] = {}
        for row in result:
            project_pool = pools.setdefault(str(row.project_id), {})
            project_pool[str(row.chunk_id)] = row.chunk_text
        return pools
    
    async def _random_negative_rows(
        self,
        project_id: UUID,
        *,
        excluded_chunk_ids: set[UUID],
        limit_per_project: int,
    ):
        stmt = (
            select(ChunkModel.id, ChunkModel.text)
            .join(VideoModel, ChunkModel.video_id == VideoModel.id)
            .join(ProjectModel, VideoModel.project_id == ProjectModel.id)
            .where(
                ProjectModel.id == project_id,
                ProjectModel.search_serving_state == "SERVABLE",
                VideoModel.project_id == project_id,
                VideoModel.user_id == ProjectModel.user_id,
                VideoModel.status == "READY",
            )
            .order_by(func.random())
            .limit(limit_per_project)
        )
        if excluded_chunk_ids:
            stmt = stmt.where(ChunkModel.id.not_in(excluded_chunk_ids))
        result = await self._session.execute(stmt)
        return list(result)


def _parse_uuid_set(values: set[str]) -> set[UUID]:
    return {parsed for value in values if (parsed := _parse_uuid(value)) is not None}


def _parse_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except ValueError:
        return None


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
        from src.infra.db.snapshot_registry import ModelSnapshotStore

        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")
        if release.candidate_model_version is None or release.candidate_index_name is None:
            raise ValueError("candidate release fields are required for cutover")

        cutover_model_version = release.candidate_model_version
        cutover_index_name = release.candidate_index_name

        previous_active_model_version = release.active_model_version
        previous_active_index_name = release.active_index_name

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

        await ModelSnapshotStore(self._session).record_cutover(
            model_version=cutover_model_version,
            index_name=cutover_index_name,
            captured_at=switched_at,
        )
        return release

    async def mark_rollback_preparing(self, *, updated_at: datetime) -> ModelReleaseModel:
        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")
        release.release_status = "ROLLBACK_PREPARING"
        release.updated_at = updated_at
        await self._session.flush()
        return release

    async def get_rollback_target(self) -> tuple[str, str] | None:
        from src.infra.db.snapshot_registry import ModelSnapshotStore

        target = await ModelSnapshotStore(self._session).get_rollback_target()
        if target is None:
            return None
        return target.model_version, target.index_name

    async def complete_rollback_restore(self, *, restored_at: datetime) -> ModelReleaseModel:
        from src.infra.db.snapshot_registry import ModelSnapshotStore

        release = await self.get_current()
        if release is None:
            raise ValueError("current ModelRelease row is required")

        snapshot_store = ModelSnapshotStore(self._session)
        target = await snapshot_store.get_rollback_target()
        if target is None:
            raise ValueError("no rollback target snapshot is available")

        await snapshot_store.record_rollback(restored_at=restored_at)

        release.active_model_version = target.model_version
        release.active_index_name = target.index_name
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

    async def get_candidate_deployment_run(self) -> MLPipelineRunModel | None:
        release = await ModelReleaseStore(self._session).get_current()
        if release is None:
            return None
        if release.release_status == "CANDIDATE_REINDEXING":
            return await self._ready_run_for_candidate(release.candidate_model_version)
        if release.release_status == "STABLE":
            return await self._ready_run_not_active_candidate(release.active_model_version)
        return None

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

    async def mark_deploy_completed(
        self,
        *,
        run_id: UUID,
        completed_at: datetime,
    ) -> None:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.status = "DEPLOY_COMPLETED"
        run.updated_at = completed_at
        await self._session.flush()

    async def record_deployment_attempt_failure(
        self,
        *,
        run_id: UUID,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        updated_at: datetime,
    ) -> int:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.deployment_attempt_count += 1
        run.last_deployment_attempt_at = updated_at
        run.failed_stage = failed_stage
        run.failure_type = failure_type
        run.failure_reason = failure_reason
        run.updated_at = updated_at
        await self._session.flush()
        return run.deployment_attempt_count

    async def mark_deployment_blocked(
        self,
        *,
        run_id: UUID,
        failed_stage: str,
        failure_type: str,
        failure_reason: str,
        blocked_at: datetime,
    ) -> None:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.status = "DEPLOYMENT_BLOCKED"
        run.failed_stage = failed_stage
        run.failure_type = failure_type
        run.failure_reason = failure_reason
        run.deployment_blocked_at = blocked_at
        run.updated_at = blocked_at
        await self._session.flush()

    async def reset_deployment_attempts(
        self,
        *,
        run_id: UUID,
        updated_at: datetime,
    ) -> None:
        run = await self.get(run_id)
        if run is None:
            raise ValueError(f"MLPipelineRun not found: {run_id}")
        run.deployment_attempt_count = 0
        run.last_deployment_attempt_at = None
        run.deployment_blocked_at = None
        run.failed_stage = None
        run.failure_type = None
        run.failure_reason = None
        run.updated_at = updated_at
        await self._session.flush()

    async def _ready_run_for_candidate(self, candidate_model_version: str | None) -> MLPipelineRunModel | None:
        if candidate_model_version is None:
            return None
        result = await self._session.execute(
            select(MLPipelineRunModel)
            .where(
                MLPipelineRunModel.status == "READY_FOR_RELEASE",
                MLPipelineRunModel.candidate_model_version == candidate_model_version,
            )
            .order_by(MLPipelineRunModel.updated_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _ready_run_not_active_candidate(self, active_model_version: str) -> MLPipelineRunModel | None:
        result = await self._session.execute(
            select(MLPipelineRunModel)
            .where(
                MLPipelineRunModel.status == "READY_FOR_RELEASE",
                MLPipelineRunModel.candidate_model_version != active_model_version,
            )
            .order_by(MLPipelineRunModel.updated_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


class ProjectRollbackStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_rollback_excluded_projects(self) -> bool:
        result = await self._session.execute(
            select(ProjectModel.id)
            .where(ProjectModel.search_serving_state == "ROLLBACK_EXCLUDED")
            .limit(1)
        )
        return result.first() is not None

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
