from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.infra.db.models import (
    Base,
    MLPipelineRunModel,
    ModelEvaluationModel,
    ModelReleaseModel,
    ProjectModel,
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as db_session:
            yield db_session
    finally:
        await engine.dispose()


async def test_project_serving_state_is_constrained_like_operational_schema(
    session: AsyncSession,
) -> None:
    session.add(ProjectModel(user_id=uuid4(), title="Project", search_serving_state="VISIBLE"))

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_ml_pipeline_run_status_and_failure_type_are_constrained_like_operational_schema(
    session: AsyncSession,
) -> None:
    session.add(
        MLPipelineRunModel(
            status="DONE",
            failure_type="NOT_A_FAILURE_TYPE",
            dataset_version="dataset-v1",
            baseline_model_version="baseline-v1",
            candidate_model_version="candidate-v1",
            created_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
            updated_at=datetime(2026, 5, 13, 10, 0, tzinfo=UTC),
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_model_evaluation_status_decision_and_sample_count_are_constrained_like_operational_schema(
    session: AsyncSession,
) -> None:
    session.add(
        ModelEvaluationModel(
            candidate_model_version="candidate-v1",
            baseline_model_version="baseline-v1",
            evaluation_dataset_ref="gs://bucket/eval.jsonl",
            sample_count=-1,
            status="DONE",
            quality_metrics={},
            pass_criteria={},
            overall_decision="MAYBE",
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_model_release_singleton_and_status_are_constrained_like_operational_schema(
    session: AsyncSession,
) -> None:
    session.add(
        ModelReleaseModel(
            singleton_key=2,
            release_status="BROKEN",
            active_model_version="model-v1",
            active_index_name="index-v1",
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_model_release_allows_only_one_singleton_row(
    session: AsyncSession,
) -> None:
    session.add_all(
        [
            ModelReleaseModel(
                release_status="STABLE",
                active_model_version="model-v1",
                active_index_name="index-v1",
            ),
            ModelReleaseModel(
                release_status="STABLE",
                active_model_version="model-v2",
                active_index_name="index-v2",
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        await session.flush()
