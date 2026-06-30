from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.core.config import Settings
from src.main import create_app
from tests.support import TEST_JWT_SIGNING_KEY


def create_settings() -> Settings:
    return Settings(
        gcp_project_id="project-id",
        gcs_video_bucket_name="video-bucket",
        jwt_secret_key=TEST_JWT_SIGNING_KEY,
        database_url="postgresql+asyncpg://user:pass@localhost:5432/app",
        broker_type="inmemory",
    )


def create_cookie_token(settings: Settings) -> str:
    return jwt.encode(
        {
            "requester_user_id": str(uuid4()),
            "exp": datetime.now(tz=timezone.utc) + timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )


def create_expired_cookie_token(settings: Settings) -> str:
    return jwt.encode(
        {
            "requester_user_id": str(uuid4()),
            "exp": datetime.now(tz=timezone.utc) - timedelta(minutes=5),
        },
        settings.jwt_secret_key,
        algorithm="HS256",
    )


@pytest.mark.asyncio
async def test_logout_requires_csrf_header_for_cookie_auth() -> None:
    settings = create_settings()
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        client.cookies.set(settings.auth_cookie_name, create_cookie_token(settings))
        client.cookies.set(settings.csrf_cookie_name, "csrf-1")

        response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_logout_clears_cookies_when_csrf_header_matches() -> None:
    settings = create_settings()
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        client.cookies.set(settings.auth_cookie_name, create_cookie_token(settings))
        client.cookies.set(settings.csrf_cookie_name, "csrf-1")

        response = await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "csrf-1"},
        )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_logout_clears_cookies_when_jwt_is_expired_and_csrf_header_matches() -> None:
    settings = create_settings()
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        client.cookies.set(settings.auth_cookie_name, create_expired_cookie_token(settings))
        client.cookies.set(settings.csrf_cookie_name, "csrf-1")

        response = await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "csrf-1"},
        )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_logout_clears_cookies_when_jwt_is_invalid_and_csrf_header_matches() -> None:
    settings = create_settings()
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        client.cookies.set(settings.auth_cookie_name, "not-a-jwt")
        client.cookies.set(settings.csrf_cookie_name, "csrf-1")

        response = await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "csrf-1"},
        )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_logout_rejects_invalid_jwt_when_csrf_header_mismatches() -> None:
    settings = create_settings()
    app = create_app(settings)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="https://testserver",
    ) as client:
        client.cookies.set(settings.auth_cookie_name, "not-a-jwt")
        client.cookies.set(settings.csrf_cookie_name, "csrf-1")

        response = await client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": "csrf-2"},
        )

    assert response.status_code == 403
