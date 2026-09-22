from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from src.infra.inmemory_broker import InMemoryBrokerClient
from src.models.admin_ops import Project
from tests.support import AppContext, auth_headers, build_video, create_token, seed_video


async def seed_project(app_context: AppContext, project: Project) -> None:
    async with app_context.session_factory() as session:
        session.add(project)
        await session.commit()


async def seed_search_snapshot(app_context: AppContext, *, project_id: UUID, user_id: UUID) -> None:
    async with app_context.session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO search_response_snapshot (
                    req_id,
                    user_id,
                    project_id,
                    query_text,
                    topk_chunk_ids,
                    used_chunk_ids,
                    active_model_version,
                    active_index_name,
                    served_vector_paths,
                    project_serving_state,
                    expires_at
                )
                VALUES (
                    :req_id,
                    :user_id,
                    :project_id,
                    'query',
                    '[]'::jsonb,
                    '[]'::jsonb,
                    'model-v1',
                    'index-v1',
                    '[]'::jsonb,
                    'SERVABLE',
                    :expires_at
                )
                """
            ),
            {
                "req_id": uuid4(),
                "user_id": user_id,
                "project_id": project_id,
                "expires_at": datetime.now(tz=timezone.utc) + timedelta(days=1),
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_create_and_list_projects_for_authenticated_user(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    await seed_project(
        app_context,
        Project(id=uuid4(), user_id=uuid4(), title="Foreign project"),
    )
    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))

    create_response = await api_client.post(
        "/api/v1/projects",
        headers=auth_headers(token),
        json={"title": "강의 프로젝트"},
    )
    list_response = await api_client.get(
        "/api/v1/projects",
        headers=auth_headers(token),
    )

    assert create_response.status_code == 201
    created = create_response.json()
    assert created["title"] == "강의 프로젝트"
    assert created["videoCount"] == 0
    assert created["createdAt"]
    assert created["updatedAt"]
    assert list_response.status_code == 200
    assert [project["id"] for project in list_response.json()] == [created["id"]]


@pytest.mark.asyncio
async def test_project_video_list_is_scoped_by_project_and_owner(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    owner_id = uuid4()
    other_user_id = uuid4()
    owner_project_id = uuid4()
    other_owner_project_id = uuid4()
    foreign_project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=owner_project_id, user_id=owner_id, title="Owner project"),
    )
    await seed_project(
        app_context,
        Project(id=other_owner_project_id, user_id=owner_id, title="Other owner project"),
    )
    await seed_project(
        app_context,
        Project(id=foreign_project_id, user_id=other_user_id, title="Foreign project"),
    )
    await seed_video(
        app_context.session_factory,
        build_video(user_id=owner_id, project_id=owner_project_id, title="Visible video"),
    )
    await seed_video(
        app_context.session_factory,
        build_video(user_id=owner_id, project_id=other_owner_project_id, title="Other project video"),
    )
    await seed_video(
        app_context.session_factory,
        build_video(user_id=other_user_id, project_id=foreign_project_id, title="Foreign video"),
    )

    token = create_token(app_context.settings.jwt_secret_key, str(owner_id))
    response = await api_client.get(
        f"/api/v1/projects/{owner_project_id}/videos",
        headers=auth_headers(token),
    )
    foreign_response = await api_client.get(
        f"/api/v1/projects/{foreign_project_id}/videos",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["title"] for item in body["items"]] == ["Visible video"]
    assert body["next_cursor"] is None
    assert foreign_response.status_code == 404


@pytest.mark.asyncio
async def test_update_project_title_for_owner(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=project_id, user_id=requester_user_id, title="Old title"),
    )
    await seed_video(
        app_context.session_factory,
        build_video(user_id=requester_user_id, project_id=project_id),
    )

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    response = await api_client.patch(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers(token),
        json={"title": "새 제목"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "새 제목"
    assert body["videoCount"] == 1


@pytest.mark.asyncio
async def test_update_foreign_project_returns_not_found(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    other_user_id = uuid4()
    foreign_project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=foreign_project_id, user_id=other_user_id, title="Foreign project"),
    )

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    response = await api_client.patch(
        f"/api/v1/projects/{foreign_project_id}",
        headers=auth_headers(token),
        json={"title": "새 제목"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_project_with_blank_title_is_rejected(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=project_id, user_id=requester_user_id, title="Old title"),
    )

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    response = await api_client.patch(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers(token),
        json={"title": ""},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_project_being_deleted_is_conflict(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    await seed_project(
        app_context,
        Project(
            id=project_id,
            user_id=requester_user_id,
            title="Deleting project",
            lifecycle_state="DELETING",
        ),
    )

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    response = await api_client.patch(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers(token),
        json={"title": "새 제목"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_delete_project_marks_project_deleting_and_hides_it_immediately(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=project_id, user_id=requester_user_id, title="Has videos"),
    )
    await seed_video(
        app_context.session_factory,
        build_video(user_id=requester_user_id, project_id=project_id),
    )

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    delete_response = await api_client.delete(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers(token),
    )
    list_response = await api_client.get(
        "/api/v1/projects",
        headers=auth_headers(token),
    )

    assert delete_response.status_code == 202
    assert list_response.json() == []
    assert app_context.app.state.container.broker_client.published_messages[0]["message_type"] == "PROJECT_DELETE_REQUEST"
    assert app_context.app.state.container.broker_client.published_messages[0]["project_id"] == str(project_id)

    async with app_context.session_factory() as session:
        result = await session.execute(
            text("SELECT lifecycle_state FROM project WHERE id = :project_id"),
            {"project_id": project_id},
        )
        assert result.scalar_one() == "DELETING"
        video_status = await session.scalar(
            text("SELECT status FROM video WHERE project_id = :project_id"),
            {"project_id": project_id},
        )
        assert video_status == "DELETING"

@pytest.mark.asyncio
async def test_delete_project_with_search_snapshots_is_accepted_for_worker_cascade(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=project_id, user_id=requester_user_id, title="Snapshot project"),
    )
    await seed_search_snapshot(
        app_context,
        project_id=project_id,
        user_id=requester_user_id,
    )

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    response = await api_client.delete(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers(token),
    )
    list_response = await api_client.get(
        "/api/v1/projects",
        headers=auth_headers(token),
    )

    assert response.status_code == 202
    assert list_response.json() == []


@pytest.mark.asyncio
async def test_delete_project_hides_foreign_projects(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    other_user_id = uuid4()
    foreign_project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=foreign_project_id, user_id=other_user_id, title="Foreign project"),
    )

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    response = await api_client.delete(
        f"/api/v1/projects/{foreign_project_id}",
        headers=auth_headers(token),
    )
    foreign_token = create_token(app_context.settings.jwt_secret_key, str(other_user_id))
    list_response = await api_client.get(
        "/api/v1/projects",
        headers=auth_headers(foreign_token),
    )

    assert response.status_code == 404
    assert [project["id"] for project in list_response.json()] == [str(foreign_project_id)]


@pytest.mark.asyncio
async def test_delete_project_restores_active_state_when_broker_publish_fails(
    app_context: AppContext,
    api_client: AsyncClient,
) -> None:
    requester_user_id = uuid4()
    project_id = uuid4()
    await seed_project(
        app_context,
        Project(id=project_id, user_id=requester_user_id, title="Recover on publish failure"),
    )
    video = build_video(user_id=requester_user_id, project_id=project_id)
    await seed_video(app_context.session_factory, video)
    app_context.app.state.container.broker_client = InMemoryBrokerClient(failures_before_success=3)

    token = create_token(app_context.settings.jwt_secret_key, str(requester_user_id))
    response = await api_client.delete(
        f"/api/v1/projects/{project_id}",
        headers=auth_headers(token),
    )
    list_response = await api_client.get(
        "/api/v1/projects",
        headers=auth_headers(token),
    )

    assert response.status_code == 500
    assert [project["id"] for project in list_response.json()] == [str(project_id)]
    async with app_context.session_factory() as session:
        assert await session.scalar(
            text("SELECT status FROM video WHERE id = :video_id"),
            {"video_id": video.id},
        ) == video.status
