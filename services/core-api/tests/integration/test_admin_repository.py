"""Smoke test for AdminRepository projection reads.

Ensures the foundation repository skeleton can query the admin-ops tables
through SQLAlchemy without schema drift.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from src.infra.db.admin_repository import AdminRepository
from tests.support import SessionFactory


@pytest.mark.asyncio
async def test_admin_repository_returns_project_projection(
    session_factory: SessionFactory,
) -> None:
    project_id = uuid4()
    user_id = uuid4()
    async with session_factory() as session:
        await session.execute(
            text(
                "INSERT INTO project (id, user_id, title, search_serving_state) "
                "VALUES (:id, :user_id, 'Test', 'ROLLBACK_EXCLUDED')"
            ),
            {"id": project_id, "user_id": user_id},
        )
        await session.commit()

        repo = AdminRepository(session)
        projection = await repo.get_project(project_id)

    assert projection is not None
    assert projection.id == project_id
    assert projection.user_id == user_id
    assert projection.search_serving_state == "ROLLBACK_EXCLUDED"


@pytest.mark.asyncio
async def test_admin_repository_returns_none_for_missing_run(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as session:
        repo = AdminRepository(session)
        result = await repo.get_ml_pipeline_run(uuid4())

    assert result is None
